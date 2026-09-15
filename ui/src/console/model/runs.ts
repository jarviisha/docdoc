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

/** One row. The projection FR-019 fixes, and nothing from a result. */
export interface RunSummary {
  run_id: string;
  status: RunStatus;
  created_at: string;
  finished_at: string | null;
  blob_id: string;
  schema_identity: string;
  processing_id: string | null;
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
  status: RunStatus | null;
  /** Cursors already used, so "back" is a pop. The first page has none. */
  stack: string[];
  listing: Listing;
}

export function initialListing(): ListingState {
  return { status: null, stack: [], listing: { state: "loading" } };
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
  return { status, stack: [], listing: { state: "loading" } };
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
  return state.status === null
    ? "This tenant has no runs."
    : `This tenant has no ${state.status} runs.`;
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
