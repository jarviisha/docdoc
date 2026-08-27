/**
 * Which pages render up front, and how the rest stay reachable.
 *
 * The deployment's default page limit is **1000** (`DEFAULT_MAX_PAGES`), so a
 * thousand-page document is ordinary rather than abusive and rendering every
 * page eagerly fails on documents docdoc itself accepts. Up-front work is
 * therefore bounded by the number of pages the *result* names — the count of
 * pages carrying located values — and not by the length of the document
 * (FR-051, FR-053, research R3).
 *
 * The other half is FR-052, and it is the half a bound like this usually loses:
 * every other page must stay reachable. A viewer that shows only the pages with
 * values, and offers no way to the rest, has quietly redefined the document as
 * its own summary of it.
 */

import { viewOf, type RunState } from "./state.ts";
import type { RunView } from "./types.ts";

export interface PageRequests {
  /** Pages that must be rendered before any navigation (FR-051). */
  readonly upFront: readonly number[];
  /** Pages additionally asked for since (FR-052). */
  readonly onDemand: readonly number[];
}

export function initialRequests(view: RunView): PageRequests {
  return { upFront: [...view.pagesToRender], onDemand: [] };
}

/** Every page index the document has — all of them reachable (FR-052). */
export function reachablePages(view: RunView): number[] {
  return Array.from({ length: view.pageCount }, (_unused, index) => index);
}

/**
 * Ask for one more page.
 *
 * `upFront` never grows, which is the invariant that keeps FR-053's bound and
 * FR-052's reachability from cancelling each other out: satisfying either by
 * itself is easy, and doing so breaks the other.
 */
export function requestPage(requests: PageRequests, pageIndex: number): PageRequests {
  if (requests.upFront.includes(pageIndex) || requests.onDemand.includes(pageIndex)) {
    return requests;
  }
  return { upFront: requests.upFront, onDemand: [...requests.onDemand, pageIndex] };
}

/** Everything that should currently be rendered, in page order. */
export function rendered(requests: PageRequests): number[] {
  return [...new Set([...requests.upFront, ...requests.onDemand])].sort((a, b) => a - b);
}

/**
 * What the interface must say out loud when it is not showing everything
 * (FR-054).
 *
 * A reader who takes "the pages with values on them" for "the whole document"
 * has been misled by a viewer whose entire purpose is telling them what is
 * really there — so the sentence is produced here, where it can be tested,
 * rather than left to a component.
 */
export function selectivityNotice(view: RunView): string | null {
  const shown = view.pagesToRender.length;
  if (shown >= view.pageCount) return null;
  return (
    `Showing ${shown} of ${view.pageCount} pages — those carrying located values. ` +
    "Every other page is reachable."
  );
}

/**
 * The page requests a state's result calls for, or `null` when none does.
 *
 * **This exists so the decision is testable** (T105). "Which pages should be
 * asked for right now?" was answered by a `useEffect` in a component — the one
 * layer nothing tests — and it answered wrongly for every completed run without
 * a single check going red. Moving it here makes it a pure function of the
 * state, which is what FR-043 asks for and what lets `pages.test.ts` assert that
 * a finished run asks for the pages its result names.
 *
 * A run in flight asks for nothing: it has no result yet. A failed run asks for
 * whatever its surviving stages produced, because those values are drawn on
 * pages like any other (FR-025).
 */
export function requestsForResult(state: RunState): PageRequests | null {
  const view = viewOf(state);
  return view === null ? null : initialRequests(view);
}
