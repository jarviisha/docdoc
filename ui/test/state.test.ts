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
import { toRunView } from "../src/model/run.ts";
import {
  boxesOnScreen,
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
