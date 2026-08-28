/**
 * The run state machine, and the two doors onto one invisible failure.
 *
 * A rectangle from one document drawn over another document's page is the worst
 * thing this feature can do, because it renders without error and looks exactly
 * like a correct answer. It can be reached two ways — by choosing a new document
 * while a result is on screen, and by an in-flight run for the previous document
 * landing after the choice — and both are closed here rather than in a
 * component, because a component closing them is a decision outside the tested
 * surface (FR-028, FR-049, SC-011, SC-015).
 *
 * **Elapsed time is an input, not a reading.** Nothing here calls a clock: a
 * tick arrives with the elapsed milliseconds already measured. That is what
 * makes "no progress proportion is ever derived" testable without waiting for
 * one.
 */

import type { RunView } from "./types.ts";
import type { FailureView } from "./failure.ts";

/**
 * Every state after a run starts carries **the token of the run it belongs to**.
 *
 * Not decoration. A component that holds a result beside this state — its own
 * copy of the banner text, its own copy of which pages to render — has no way to
 * ask "is this still the current run?", and so cannot discard what this machine
 * discarded. That is exactly what happened: `reduce` refused a stale failure and
 * the component put the banner up anyway, because the banner was a second
 * variable with no token in it (T103).
 *
 * With the token here, "which run is this?" has one answer and anything derived
 * from the state inherits it.
 */
export type RunState =
  | { kind: "idle" }
  | { kind: "running"; token: number; elapsedMs: number }
  | { kind: "complete"; token: number; view: RunView }
  | { kind: "failed"; token: number; failure: FailureView };

export type Event =
  | { type: "document-chosen" }
  | { type: "run-started"; token: number }
  | { type: "tick"; elapsedMs: number }
  | { type: "result"; token: number; view: RunView }
  | { type: "failure"; token: number; failure: FailureView };

export const initial: RunState = { kind: "idle" };

export function reduce(state: RunState, event: Event): RunState {
  switch (event.type) {
    case "document-chosen":
      // Clears the prior view **before** any new box entry can exist, and
      // invalidates whatever is in flight by leaving no token to match (FR-028).
      return { kind: "idle" };

    case "run-started":
      return { kind: "running", token: event.token, elapsedMs: 0 };

    case "tick":
      // Only the elapsed time changes. No proportion, percentage, stage count or
      // estimate is derived — the architecture knows none of them, and an
      // invented one is the same category of error as an invented location
      // (FR-045, FR-046).
      return state.kind === "running" ? { ...state, elapsedMs: event.elapsedMs } : state;

    case "result":
      if (state.kind !== "running" || state.token !== event.token) return state;
      return { kind: "complete", token: event.token, view: event.view };

    case "failure":
      if (state.kind !== "running" || state.token !== event.token) return state;
      return { kind: "failed", token: event.token, failure: event.failure };
  }
}

/**
 * Whether a second run may be started (FR-049).
 *
 * Not merely a disabled button: a result arriving for a document that is no
 * longer selected is discarded by the token rule above, so this is the half that
 * keeps the user from generating the situation in the first place.
 */
export function canStartRun(state: RunState): boolean {
  return state.kind !== "running";
}

/**
 * What to show while waiting. Elapsed time and nothing more.
 *
 * There is deliberately no `cancel`: closing the browser stops the waiting, not
 * the run, and the provider is paid either way. A control promising otherwise
 * would be this interface's first lie (FR-047).
 */
export function waitingLabel(state: RunState): string | null {
  if (state.kind !== "running") return null;
  const seconds = Math.floor(state.elapsedMs / 1000);
  return `Running — ${seconds}s elapsed`;
}

/**
 * The values to show, whether the run finished or failed part way (FR-025).
 *
 * A failed run's surviving stages are an ordinary `RunView`, so this is the one
 * place that decides which one is current and the rendering layer never asks.
 * Before T091 a component read `state.kind === "complete" ? state.view : null`,
 * which is why a mid-run failure put nothing but a sentence on screen: the
 * survivors existed in the response and no code path could reach them.
 */
export function viewOf(state: RunState): RunView | null {
  if (state.kind === "complete") return state.view;
  if (state.kind === "failed") return state.failure.survivors;
  return null;
}

/**
 * The failure to show, or `null` — **derived, never held alongside** (T103).
 *
 * The rule this exists to enforce: a discarded run does not get to speak. The
 * component used to keep the banner in its own `failure` string, set from the
 * response *before* `reduce` had judged the token, so a run for a document the
 * user had already replaced still announced itself. `reduce` had discarded it;
 * the sentence about it appeared regardless.
 *
 * Reading it from the state instead makes that impossible rather than merely
 * fixed. There is one place a failure can live, the token guards entry to it, and
 * anything a renderer shows about a failure comes from here.
 */
export function failureOf(state: RunState): FailureView | null {
  return state.kind === "failed" ? state.failure : null;
}

/**
 * The identity of the run whose **result is on screen**, or `null` if none is.
 *
 * A renderer resetting per-run scaffolding — which pages have been asked for —
 * keys on this. It has to satisfy two conditions at once, and they pull in
 * opposite directions: it must **change when a result arrives**, so the
 * scaffolding is rebuilt for it, and it must **not change when the selection
 * changes**, or the reader's on-demand pages are thrown away every time they
 * click a row.
 *
 * **The predecessor of this function satisfied only the second** (T105). It
 * returned `state.token` for every state after `idle`, including `running` — and
 * `reduce` carries the same token from `run-started` into `result`, so
 * `running(42) → complete(42)` changed nothing. A renderer keyed on it never
 * rebuilt anything: its last run was during `running`, when the view was still
 * `null`, so it cleared the page requests and left them cleared. The viewer
 * listed every value and drew no page and no rectangle, which is US1 entire.
 *
 * Its docstring said "or `null` before any has finished" while its body returned
 * a token for a run still in flight. The sentence was right and the code was not.
 * `running` yields `null` here because a run in flight has produced no result,
 * which is the whole distinction the name now carries.
 */
export function resultIdOf(state: RunState): number | null {
  return state.kind === "complete" || state.kind === "failed" ? state.token : null;
}

/**
 * Any boxes currently on screen. Used to assert that none outlive their run.
 *
 * Counts a failed run's survivors too — they are boxes on a page like any
 * other, and SC-011's guarantee that no run's rectangles outlive it would have
 * a hole in it the moment a partial result was exempt from the count.
 */
export function boxesOnScreen(state: RunState): number {
  const view = viewOf(state);
  if (view === null) return 0;
  return view.values.reduce((total, row) => total + row.boxes.length, 0);
}
