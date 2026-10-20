/**
 * How often the overview refreshes itself while open.
 *
 * Two decisions, both too small to carry a component's own state yet real
 * enough to need a test: how long to wait before the next poll, and whether
 * the tab becoming visible again is itself a reason to refresh immediately
 * rather than waiting out the rest of the interval. Polling a hidden tab is
 * traffic spent on a screen nobody is reading; `document.visibilityState` is
 * the signal a component reads and hands in here as `hidden`.
 */

/** How often the overview polls while the tab is visible. */
export const POLL_MS = 5000;

/** The delay before the next poll, or `null` to stop polling while hidden. */
export function nextPoll(hidden: boolean): number | null {
  return hidden ? null : POLL_MS;
}

/**
 * Whether the tab has just become visible — which means refresh now, rather
 * than wait out whatever is left of the last interval, since that interval
 * was never running while hidden.
 */
export function onVisible(wasHidden: boolean, hidden: boolean): boolean {
  return wasHidden && !hidden;
}
