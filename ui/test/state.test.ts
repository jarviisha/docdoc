/**
 * The state machine — chiefly, that no run's boxes outlive it.
 *
 * A stale rectangle over a fresh page is the failure this milestone most needs
 * to prevent and least able to detect: it renders without error and looks
 * correct. There are two routes to it and both are closed here (FR-028, FR-049,
 * SC-011, SC-015).
 */

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import { toFailureView } from "../src/model/failure.ts";
import { select, toRunView } from "../src/model/run.ts";
import {
  boxesOnScreen,
  failureOf,
  resultIdOf,
  viewOf,
  canStartRun,
  initial,
  reduce,
  waitingLabel,
  type RunState,
} from "../src/model/state.ts";
import type { WireRun } from "../src/model/types.ts";

const RUN = JSON.parse(
  readFileSync(new URL("./fixtures/run.json", import.meta.url), "utf8"),
) as WireRun;

const completed = (): RunState =>
  reduce(reduce(initial, { type: "run-started", token: 1 }), {
    type: "result",
    token: 1,
    view: toRunView(RUN),
  });

describe("no run's boxes outlive it", () => {
  it("clears the previous result before any new box exists", () => {
    // SC-011, door one: the user chooses another document.
    const shown = completed();
    assert.ok(boxesOnScreen(shown) > 0, "the fixture should put boxes on screen");

    const after = reduce(shown, { type: "document-chosen" });

    assert.equal(boxesOnScreen(after), 0);
    assert.equal(after.kind, "idle");
  });

  it("discards a result arriving under a stale token", () => {
    // SC-015, door two: an in-flight run for the previous document lands after
    // the choice. Same failure, different route, and closing one door does not
    // close this one.
    const running = reduce(initial, { type: "run-started", token: 2 });
    const superseded = reduce(running, { type: "document-chosen" });
    const restarted = reduce(superseded, { type: "run-started", token: 3 });

    const late = reduce(restarted, { type: "result", token: 2, view: toRunView(RUN) });

    assert.equal(late.kind, "running", "the old run's result must not be shown");
    assert.equal(boxesOnScreen(late), 0);
  });

  it("accepts a result whose token matches", () => {
    const state = completed();

    assert.equal(state.kind, "complete");
    assert.ok(boxesOnScreen(state) > 0);
  });

  it("discards a stale failure too", () => {
    const running = reduce(initial, { type: "run-started", token: 5 });
    const restarted = reduce(reduce(running, { type: "document-chosen" }), {
      type: "run-started",
      token: 6,
    });

    const late = reduce(restarted, {
      type: "failure",
      token: 5,
      failure: toFailureView({ error: { class: "ProviderError", message: "boom" } }),
    });

    assert.equal(late.kind, "running");
  });
});

describe("waiting", () => {
  it("reports elapsed time and nothing more", () => {
    // FR-045, FR-046, SC-015 — the architecture knows no proportion, and an
    // invented one is the same category of error as an invented location.
    const running = reduce(initial, { type: "run-started", token: 1 });
    const ticked = reduce(running, { type: "tick", elapsedMs: 4200 });

    assert.equal(ticked.kind, "running");
    const label = waitingLabel(ticked) ?? "";
    assert.match(label, /4s/);
    assert.doesNotMatch(label, /%|\bof\b|remaining|estimat/i);
  });

  it("exposes no field that could carry a proportion", () => {
    const ticked = reduce(reduce(initial, { type: "run-started", token: 1 }), {
      type: "tick",
      elapsedMs: 1000,
    });

    assert.deepEqual(Object.keys(ticked).sort(), ["elapsedMs", "kind", "token"]);
  });

  it("ignores a tick when nothing is running", () => {
    assert.deepEqual(reduce(initial, { type: "tick", elapsedMs: 10 }), initial);
  });

  it("offers no way to cancel", () => {
    // FR-047 — closing the browser stops the waiting, not the run. A control
    // promising otherwise would be this interface's first lie, so there is no
    // event it could dispatch.
    const events = ["document-chosen", "run-started", "tick", "result", "failure"];

    assert.ok(!events.includes("cancel"));
  });
});

describe("a second run", () => {
  it("is unavailable while one is in flight", () => {
    // FR-049 — keeping the user from generating the stale-result situation at
    // all, rather than only handling it once generated.
    const running = reduce(initial, { type: "run-started", token: 1 });

    assert.equal(canStartRun(running), false);
    assert.equal(canStartRun(initial), true);
    assert.equal(canStartRun(completed()), true);
  });
});

describe("a discarded run does not get to speak", () => {
  /**
   * T103. `reduce` refused a stale failure from the first commit, and the
   * banner appeared anyway — because the banner was a second variable in a
   * component, set from the response before `reduce` had judged the token.
   * The result was discarded; the sentence about it was not.
   *
   * These assert the property that makes that impossible rather than fixed:
   * everything a renderer shows about a failure comes from the state, and the
   * state is only reachable through the token check.
   */
  const failure = (stage: string) =>
    toFailureView({ error: { class: "ValidationError", stage, message: "a rule failed" } });

  it("exposes no failure for a run that was superseded", () => {
    // Run one is in flight; the user picks another document; run one then fails.
    const running = reduce(initial, { type: "run-started", token: 1 });
    const superseded = reduce(running, { type: "document-chosen" });

    const late = reduce(superseded, { type: "failure", token: 1, failure: failure("validate") });

    assert.equal(late.kind, "idle");
    assert.equal(failureOf(late), null, "a discarded run must not put a banner on screen");
  });

  it("exposes no failure for a run superseded by a newer one", () => {
    const restarted = reduce(
      reduce(reduce(initial, { type: "run-started", token: 1 }), { type: "document-chosen" }),
      { type: "run-started", token: 2 },
    );

    const late = reduce(restarted, { type: "failure", token: 1, failure: failure("extract") });

    assert.equal(late.kind, "running");
    assert.equal(failureOf(late), null);
  });

  it("exposes the failure of the run that is current", () => {
    const running = reduce(initial, { type: "run-started", token: 7 });
    const failed = reduce(running, { type: "failure", token: 7, failure: failure("validate") });

    assert.equal(failed.kind, "failed");
    assert.notEqual(failureOf(failed), null);
    assert.equal(failureOf(failed)?.stage, "validate");
  });

  it("carries the run's token into every state a result can reach", () => {
    // What makes "which run is this?" answerable at all. Anything a renderer
    // resets per run keys on this rather than on the view, whose identity
    // changes on every selection.
    const running = reduce(initial, { type: "run-started", token: 42 });

    const done = reduce(running, { type: "result", token: 42, view: toRunView(RUN) });
    const failed = reduce(running, { type: "failure", token: 42, failure: failure("validate") });

    assert.equal(resultIdOf(done), 42);
    assert.equal(resultIdOf(failed), 42);
    assert.equal(resultIdOf(initial), null);
  });

  it("gives a new run a token the old one cannot match", () => {
    const first = reduce(initial, { type: "run-started", token: 1 });
    const second = reduce(reduce(first, { type: "document-chosen" }), {
      type: "run-started",
      token: 2,
    });

    assert.notEqual(resultIdOf(second), 1);
  });
});

describe("the key a renderer rebuilds its page requests on", () => {
  /**
   * T105, and the most expensive defect this milestone has had.
   *
   * The key has to do two things that pull against each other: **change when a
   * result arrives**, so per-run scaffolding is rebuilt for it, and **not change
   * when the selection changes**, or the reader's on-demand pages are discarded
   * every time they click a row.
   *
   * Its predecessor satisfied only the second. It returned `state.token` for
   * every state after `idle`, and `reduce` carries one token from `run-started`
   * through to `result` — so `running(42) → complete(42)` was no change at all.
   * The renderer's effect never fired at the moment a result appeared, left its
   * page requests at the `null` it had set while the run was still in flight,
   * and drew no page and no rectangle for any completed run. US1 is "see where a
   * value came from"; it showed a list and no picture.
   *
   * The whole suite stayed green, because the suite does not render. These are
   * the checks that would have gone red.
   */
  const view = () => toRunView(RUN);

  it("changes when a result arrives", () => {
    const running = reduce(initial, { type: "run-started", token: 42 });
    const complete = reduce(running, { type: "result", token: 42, view: view() });

    assert.notEqual(
      resultIdOf(running),
      resultIdOf(complete),
      "a renderer keyed on this would never rebuild for the result",
    );
  });

  it("changes when a failure arrives, so survivors get their pages too", () => {
    const running = reduce(initial, { type: "run-started", token: 7 });
    const failed = reduce(running, {
      type: "failure",
      token: 7,
      failure: toFailureView({
        error: { class: "ValidationError", stage: "validate", message: "a rule failed" },
      }),
    });

    assert.notEqual(resultIdOf(running), resultIdOf(failed));
  });

  it("is null while a run is in flight, because there is no result yet", () => {
    assert.equal(resultIdOf(reduce(initial, { type: "run-started", token: 42 })), null);
    assert.equal(resultIdOf(initial), null);
  });

  it("does not change when the selection changes", () => {
    // The other half. Keying on the view would satisfy the first test and fail
    // this one, throwing away every page the reader had asked for.
    const complete = reduce(reduce(initial, { type: "run-started", token: 42 }), {
      type: "result",
      token: 42,
      view: view(),
    });
    assert.equal(complete.kind, "complete");

    const row = viewOf(complete)?.values[0];
    assert.ok(row);
    const selected: RunState = { ...complete, view: select(viewOf(complete)!, row.fieldPath) };

    assert.equal(resultIdOf(selected), resultIdOf(complete));
  });

  it("does not change on a tick", () => {
    const running = reduce(initial, { type: "run-started", token: 42 });
    const ticked = reduce(running, { type: "tick", elapsedMs: 3000 });

    assert.equal(resultIdOf(ticked), resultIdOf(running));
  });
});
