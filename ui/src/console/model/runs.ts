/**
 * The run listing's state, and the paging that makes it exact.
 *
 * **Three states, not two** (Edge Cases): loading, loaded, and failed. An empty
 * tenant and a failed request look identical if a list is only "the rows I have"
 * — and telling an operator there are no runs when the request was refused is the
 * failure this separation exists to prevent.
 *
 * **The cursor stack lives here.** The route offers a forward cursor only, and
 * keeping the visited ones is what makes "back" a pop rather than a second
 * cursor direction the server would have to encode and order consistently. Half
 * the route's work, held in the page that needs it.
 */

import type { RunStatus } from "./client.ts";
import type { Failure } from "./failure.ts";

/** The two things a run can be. ADR-0023 §7 splits the console's cards by it. */
export type RunKind = "ocr" | "extraction";

/** One row. The projection FR-019 fixes, and nothing from a result. */
export interface RunSummary {
  run_id: string;
  /** Which tenant owns the run: the console lists every tenant's (ADR-0023 §3). */
  tenant_id: string;
  status: RunStatus;
  kind: RunKind;
  created_at: string;
  /** The most recent claim, or `null` before one happens. */
  started_at: string | null;
  finished_at: string | null;
  blob_id: string;
  /** The extraction schema, or `null` for an OCR run, which has none. */
  schema_identity: string | null;
  processing_id: string | null;
  /** The OCR parser that claimed the run, or `null` before one does. */
  ocr_parser: string | null;
  /** The typed error's class name, never a message (FR-039). */
  error_class: string | null;
  /** A member of that error class's own `REASONS`, never free text (ADR-0023 §7). */
  error_reason: string | null;
}

export interface RunPage {
  runs: RunSummary[];
  next_cursor: string | null;
}

export type Listing =
  | { state: "loading" }
  | { state: "loaded"; page: RunPage }
  | { state: "failed"; failure: Failure };

export interface ListingState {
  /** One tenant's runs, or `null` for every tenant's. */
  tenant: string | null;
  status: RunStatus | null;
  kind: RunKind | null;
  /** Cursors already used, so "back" is a pop. The first page has none. */
  stack: string[];
  listing: Listing;
}

export function initialListing(): ListingState {
  return { tenant: null, status: null, kind: null, stack: [], listing: { state: "loading" } };
}

/** Whether there is a previous page to return to. */
export function canGoBack(state: ListingState): boolean {
  return state.stack.length > 0;
}

/** Whether the current page offers a next one. */
export function canGoForward(state: ListingState): boolean {
  return state.listing.state === "loaded" && state.listing.page.next_cursor !== null;
}

/** The cursor the next request should carry, or `undefined` for page one. */
export function currentCursor(state: ListingState): string | undefined {
  return state.stack.at(-1);
}

export function forward(state: ListingState): ListingState {
  if (state.listing.state !== "loaded") return state;
  const next = state.listing.page.next_cursor;
  if (next === null) return state;
  return { ...state, stack: [...state.stack, next], listing: { state: "loading" } };
}

export function back(state: ListingState): ListingState {
  if (state.stack.length === 0) return state;
  return { ...state, stack: state.stack.slice(0, -1), listing: { state: "loading" } };
}

/**
 * Changing the filter returns to the first page.
 *
 * A cursor is a position in one ordering, and the ordering a filter produces is
 * a different one — carrying it across would page into a sequence it was never
 * issued for. The route would refuse nothing, because the cursor is this
 * tenant's; it would simply skip rows nobody could explain.
 */
export function filtered(state: ListingState, status: RunStatus | null): ListingState {
  return { ...state, status, stack: [], listing: { state: "loading" } };
}

/**
 * Narrow to one kind, or widen back to both with `null` — the cursor stack
 * resets for the same reason `filtered` resets it: a kind change is a
 * different ordering, and the cursor is bound to the one it was issued for.
 */
export function byKind(state: ListingState, kind: RunKind | null): ListingState {
  return { ...state, kind, stack: [], listing: { state: "loading" } };
}

/**
 * Narrow to one tenant, or widen back to all with a blank, for the same
 * reason `filtered` returns to page one: the server binds a cursor to the
 * tenant filter that produced it.
 */
export function forTenant(state: ListingState, tenant: string): ListingState {
  const trimmed = tenant.trim();
  return { ...state, tenant: trimmed === "" ? null : trimmed, stack: [], listing: { state: "loading" } };
}

/** Read the current page again, from where it is. */
export function reloaded(state: ListingState): ListingState {
  return { ...state, listing: { state: "loading" } };
}

export function loaded(state: ListingState, page: RunPage): ListingState {
  return { ...state, listing: { state: "loaded", page } };
}

/**
 * A failed page, and the stack reset with it.
 *
 * The stack describes positions in a listing this page does not have; keeping it
 * would offer a "back" that pages into a sequence never established.
 */
export function failed(state: ListingState, failure: Failure): ListingState {
  return { ...state, stack: [], listing: { state: "failed", failure } };
}

/** Whether the loaded page is genuinely empty, rather than merely unloaded. */
export function isEmpty(state: ListingState): boolean {
  return state.listing.state === "loaded" && state.listing.page.runs.length === 0;
}

/**
 * What an empty list says.
 *
 * A filter that matched nothing and a tenant with no runs are different facts,
 * and "no results found" over a filter nobody set is the sentence that made this
 * function exist (Edge Cases).
 */
export function emptyNotice(state: ListingState): string {
  const who = state.tenant === null ? "No tenant has" : `Tenant ${state.tenant} has`;
  const what = [state.status, state.kind === null ? null : KIND_WORD[state.kind]]
    .filter((word) => word !== null)
    .join(" ");
  return what === "" ? `${who} no runs.` : `${who} no ${what} runs.`;
}

const KIND_WORD: Record<RunKind, string> = { ocr: "OCR", extraction: "extraction" };

/** A run's kind as a column shows it: an OCR run names the parser that claimed it. */
export function kindLabel(run: { kind: RunKind; ocr_parser: string | null }): string {
  if (run.kind === "extraction") return "Extraction";
  return run.ocr_parser === null ? "OCR" : `OCR (${run.ocr_parser})`;
}

/** Whether a run can still be cancelled: only before it reaches an outcome. */
export function isCancellable(status: RunStatus): boolean {
  return status === "queued" || status === "running";
}

/**
 * Whether the listing refreshes itself. Page one only: on a later page new
 * runs would shift every row under the reader, and the cursor that reached
 * the page would no longer describe it. Returning to page one resumes it.
 */
export function autoRefreshes(state: ListingState): boolean {
  return state.stack.length === 0;
}

/**
 * A background refresh's page, applied only if the listing is still the one
 * it was asked for — same filters, still page one, and no load of the
 * reader's own under way. Otherwise the answer is stale and dropped.
 */
export function polled(state: ListingState, askedFor: ListingState, page: RunPage): ListingState {
  const same =
    state.tenant === askedFor.tenant &&
    state.status === askedFor.status &&
    state.kind === askedFor.kind &&
    autoRefreshes(state) &&
    state.listing.state !== "loading";
  return same ? loaded(state, page) : state;
}

/** One run as its detail view shows it: the row, plus what only the detail body carries. */
export interface RunRecord extends RunSummary {
  ocr_language: string | null;
  worker_id: string | null;
  attempts: number | null;
}

/**
 * Read a detail body (or a cancel answer, which is the same body), or `null`
 * if it is not an object. Trusts the body's shape the way `toPage` trusts a
 * row's: it is the same projection plus three fields, from the same route
 * family. The three default to `null` so an older deployment reads as
 * "not recorded" rather than as `undefined`.
 */
export function toRecord(body: unknown): RunRecord | null {
  if (typeof body !== "object" || body === null) return null;
  const record = { ocr_language: null, worker_id: null, attempts: null, ...(body as RunSummary) };
  // A deployment older than the console's `finished_at` sends none; `undefined`
  // would reach `timeline` and parse as NaN.
  return { ...record, finished_at: record.finished_at ?? null };
}

/**
 * Whether the detail view keeps polling: only while a run can still change —
 * `queued` or `running` — and never for a `null` run, which has nothing to
 * poll for yet.
 */
export function detailPolls(run: RunRecord | null): boolean {
  return run !== null && (run.status === "queued" || run.status === "running");
}

/**
 * Whether the detail view reads and shows the run's result: a succeeded OCR
 * run only. An extraction result has no page images to sit beside, and keeps
 * only Download result.
 */
export function showsResult(run: RunRecord | null): boolean {
  return run !== null && run.status === "succeeded" && run.kind === "ocr";
}

/**
 * What the detail view says after a cancel the server accepted. A running run
 * still reads `running` until its worker reaches a stage boundary (FR-029), so
 * an unchanged badge alone would look like the click did nothing.
 */
export function cancelNotice(run: { status: RunStatus }): string | null {
  return run.status === "running"
    ? "Cancellation requested; the run stops at its next stage boundary."
    : null;
}

/** Whether a refused cancel means the run shown is stale: a `409` says it already finished. */
export function refetchAfterCancel(failure: Failure): boolean {
  return failure.kind === "reported" && failure.status === 409;
}

/**
 * What a failed run reports about itself.
 *
 * **The thing the detail view did not show.** User Story 2 scenario 7 asks that
 * an opened failed run report "the failure … in the typed, provider-neutral form
 * the API already produces", and SC-015 times an operator finding a named failed
 * run and stating *why it failed*. `GET /v1/runs/{run_id}` has carried
 * `failed_stage` and `error_class` the whole time; nothing read them, so the
 * screen showed a red badge saying `failed` and four timestamps. T073 — the one
 * check with a human in it, and the only unticked box in the milestone — is what
 * would have caught it.
 *
 * `stage` is `null` on a run that failed **before reaching a stage**, which the
 * API's own model calls a real distinction rather than a missing value: an
 * unresolvable schema fails a run without any stage having run.
 *
 * **A class name and never a message.** That is what the route sends and it is
 * the reason this is safe to show at all: a provider's message may quote the
 * document it choked on, and `RunStateResponse` carries no message field for
 * exactly that reason. Passing the class through invents no vocabulary (FR-039)
 * and discloses no content (FR-037).
 */
export interface RunFailure {
  /** The stage that failed, or `null` if the run failed before one ran. */
  stage: string | null;
  /** The typed error's class name. Never a message, never document text. */
  errorClass: string;
}

/**
 * Read a run's failure from a detail body, or `null` if it reports none.
 *
 * Keyed on `error_class` rather than on `status === "failed"`: the status says
 * the run failed and this says what the deployment recorded about it, and a
 * failed run whose class never made it into the row should show nothing rather
 * than an empty label. The two are different questions and the caller already
 * has the first.
 */
export function toFailure(body: unknown): RunFailure | null {
  if (typeof body !== "object" || body === null) return null;
  const record = body as { failed_stage?: unknown; error_class?: unknown };
  if (typeof record.error_class !== "string" || record.error_class === "") return null;
  return {
    stage: typeof record.failed_stage === "string" ? record.failed_stage : null,
    errorClass: record.error_class,
  };
}

/** Read a page from a response body without trusting its shape. */
export function toPage(body: unknown): RunPage {
  if (typeof body !== "object" || body === null) return { runs: [], next_cursor: null };
  const runs = (body as { runs?: unknown }).runs;
  const cursor = (body as { next_cursor?: unknown }).next_cursor;
  return {
    runs: Array.isArray(runs) ? (runs as RunSummary[]) : [],
    next_cursor: typeof cursor === "string" ? cursor : null,
  };
}

/**
 * How long a run waited, and how long it has run, from its own four
 * timestamps and the clock reading `now`.
 *
 * `now` arrives as a parameter rather than being read here: a running run's
 * duration only grows because the caller re-renders it, and what grows it is
 * the caller's own clock, not one this module would otherwise have to read
 * each call.
 *
 * Either figure is `null` before a claim happens — there is no wait to report
 * and no run to be timing — and negative, which a clock skewed against the
 * deployment's own can otherwise produce, is clamped to zero rather than
 * shown as a run that finished before it began.
 */
export function timeline(
  run: { created_at: string; started_at: string | null; finished_at?: string | null },
  now: number,
): { waitMs: number | null; runMs: number | null } {
  if (run.started_at === null) return { waitMs: null, runMs: null };
  const created = Date.parse(run.created_at);
  const started = Date.parse(run.started_at);
  // Missing reads as null: still going.
  const finished = run.finished_at == null ? now : Date.parse(run.finished_at);
  return {
    waitMs: Math.max(0, started - created),
    runMs: Math.max(0, finished - started),
  };
}

/**
 * Fixed copy for a run's `error_reason`, the member of an error class's own
 * `REASONS` the contract carries (never a message, ADR-0023 §7).
 *
 * An unknown reason — one a newer deployment added before this console's copy
 * caught up — shows as the raw reason string rather than nothing at all. A
 * `null` reason falls back to the error class, which is still more than an
 * operator had before T075; both null is genuinely nothing to report.
 */
const REASON_LABELS: Record<string, string> = {
  // OCR and extraction (LLM) timeouts share this reason, so no host is named.
  timeout: "The host timed out",
  deadline: "The run ran out of time",
  rate_limit: "The host rate-limited the request",
  auth: "The host refused docdoc's credentials",
  transport: "docdoc could not reach the host",
  service: "The host reported an error",
  incomplete: "The host returned fewer pages than it was sent",
  mime_type: "This file type is not supported",
  size_limit: "The file is too large",
  page_limit: "The file has too many pages",
  encrypted: "The file is encrypted",
  corrupt: "The file is corrupt",
  empty_result: "The parser found no text",
  capability_mismatch: "The parser cannot handle this input",
  invalid_order: "The parser produced an invalid document",
  wrong_document: "The parser produced an invalid document",
  internal: "The parser produced an invalid document",
};

export function reasonLabel(reason: string | null, errorClass: string | null): string | null {
  if (reason === null) return errorClass;
  return REASON_LABELS[reason] ?? reason;
}
