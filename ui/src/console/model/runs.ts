/**
 * The run listing's request, and the paging that makes it exact.
 *
 * **Three states, not two** (Edge Cases): loading, loaded, and failed. An empty
 * tenant and a failed request look identical if a list is only "the rows I have"
 * — and telling an operator there are no runs when the request was refused is the
 * failure this separation exists to prevent. The query holds the three; the
 * filters live in the URL.
 *
 * **The cursor stack's rules live here.** The route offers a forward cursor only, and
 * keeping the visited ones is what makes "back" a pop rather than a second
 * cursor direction the server would have to encode and order consistently. Half
 * the route's work, held in the page that needs it.
 */

import type { Intent, RunStatus } from "./client.ts";
import type { RunsSearch } from "./search.ts";
import { describe, type Failure } from "./failure.ts";

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

/** The run list's filters: exactly what the URL holds (`RunsSearch`). */
export type RunFilters = RunsSearch;

/** The request for one page of the list under `filters`; `cursor` is `undefined` on page one. */
export function listIntent(filters: RunFilters, cursor: string | undefined): Intent {
  return {
    type: "list-runs",
    ...(filters.tenant === undefined ? {} : { tenant: filters.tenant }),
    ...(filters.status === undefined ? {} : { status: filters.status }),
    ...(filters.kind === undefined ? {} : { kind: filters.kind }),
    ...(cursor === undefined ? {} : { cursor }),
  };
}

/*
 * The pager is a stack of the cursors already used, so "back" is a pop. The
 * first page has none. A filter change starts a new stack: a cursor is a
 * position in one ordering, and a different filter is a different ordering.
 */

/** Whether there is a previous page to return to. */
export function canGoBack(stack: string[]): boolean {
  return stack.length > 0;
}

/** The cursor the next request should carry, or `undefined` for page one. */
export function currentCursor(stack: string[]): string | undefined {
  return stack.at(-1);
}

/** The stack after Next; unchanged on a last page, which offers no cursor. */
export function forward(stack: string[], nextCursor: string | null): string[] {
  return nextCursor === null ? stack : [...stack, nextCursor];
}

/** The stack after Previous; unchanged on page one. */
export function back(stack: string[]): string[] {
  return stack.slice(0, -1);
}

/**
 * Whether the listing refreshes itself. Page one only: on a later page new
 * runs would shift every row under the reader, and the cursor that reached
 * the page would no longer describe it. Returning to page one resumes it.
 */
export function autoRefreshes(stack: string[]): boolean {
  return stack.length === 0;
}

/**
 * What an empty list says.
 *
 * A filter that matched nothing and a tenant with no runs are different facts,
 * and "no results found" over a filter nobody set is the sentence that made this
 * function exist (Edge Cases).
 */
export function emptyNotice(filters: RunFilters): string {
  const who = filters.tenant === undefined ? "No tenant has" : `Tenant ${filters.tenant} has`;
  const what = [filters.status, filters.kind === undefined ? undefined : KIND_WORD[filters.kind]]
    .filter((word) => word !== undefined)
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

/** Whether the detail view reads and shows the run's result: any succeeded run. */
export function showsResult(run: RunRecord | null): boolean {
  return run !== null && run.status === "succeeded";
}

/** Whether the detail view lists extracted fields to check: a succeeded extraction run. */
export function showsFields(run: RunRecord | null): boolean {
  return showsResult(run) && run!.kind === "extraction";
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
 * The banner a result read that failed shows. An expired result (`410
 * result_expired`) is the retention policy doing its job, not a fault, so it
 * reads as information; anything else is an error.
 */
export function resultNotice(failure: Failure): {
  status: "info" | "error";
  title: string;
  description?: string;
} {
  if (failure.kind === "reported" && failure.status === 410) {
    return { status: "info", title: "This run's result is no longer stored." };
  }
  return { status: "error", title: "The result could not be shown", description: describe(failure) };
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
