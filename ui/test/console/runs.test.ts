/**
 * T041 — paging, filtering, and the difference between empty and failed.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  back,
  canGoBack,
  canGoForward,
  currentCursor,
  emptyNotice,
  failed,
  filtered,
  forward,
  initialListing,
  isEmpty,
  loaded,
  reload,
  toFailure,
  toPage,
} from "../../src/console/model/runs.ts";

const PAGE = { runs: [{ run_id: "r1" }] as never, next_cursor: "c1" };

test("the first page carries no cursor", () => {
  const state = initialListing();
  assert.equal(currentCursor(state), undefined);
  assert.equal(canGoBack(state), false);
});

test("forward pushes the cursor and back pops it", () => {
  const first = loaded(initialListing(), PAGE);
  assert.equal(canGoForward(first), true);

  const second = forward(first);
  assert.equal(currentCursor(second), "c1");
  assert.equal(canGoBack(second), true);
  assert.equal(second.listing.state, "loading");

  const returned = back(second);
  assert.equal(currentCursor(returned), undefined);
  assert.equal(canGoBack(returned), false);
});

test("forward on a last page does nothing", () => {
  const last = loaded(initialListing(), { runs: [], next_cursor: null });
  assert.equal(canGoForward(last), false);
  assert.deepEqual(forward(last), last);
});

test("back from the first page does nothing", () => {
  const first = loaded(initialListing(), PAGE);
  assert.deepEqual(back(first), first);
});

test("changing the filter returns to the first page", () => {
  // A cursor is a position in one ordering; a filter produces a different one,
  // and carrying the cursor across pages into a sequence it was never issued for.
  const deep = forward(loaded(initialListing(), PAGE));
  const refiltered = filtered(deep, "failed");

  assert.deepEqual(refiltered.stack, []);
  assert.equal(refiltered.status, "failed");
  assert.equal(currentCursor(refiltered), undefined);
});

test("a failure resets the stack, so there is no back into a sequence that failed", () => {
  const deep = forward(loaded(initialListing(), PAGE));
  const broken = failed(deep, { kind: "transport", message: "gone" });

  assert.equal(canGoBack(broken), false);
  assert.equal(broken.listing.state, "failed");
});

test("empty and failed are different states", () => {
  const empty = loaded(initialListing(), { runs: [], next_cursor: null });
  const broken = failed(initialListing(), { kind: "transport", message: "gone" });

  assert.equal(isEmpty(empty), true);
  assert.equal(isEmpty(broken), false, "a failed request is not an empty tenant");
});

test("an empty list says which emptiness it is", () => {
  const all = loaded(initialListing(), { runs: [], next_cursor: null });
  assert.match(emptyNotice(all), /no runs/);

  const filteredEmpty = filtered(all, "failed");
  assert.match(emptyNotice(filteredEmpty), /no failed runs/);
});

test("a malformed body becomes an empty page rather than a crash", () => {
  assert.deepEqual(toPage(null), { runs: [], next_cursor: null });
  assert.deepEqual(toPage({ runs: "nope", next_cursor: 7 }), { runs: [], next_cursor: null });
  assert.deepEqual(toPage({ runs: [], next_cursor: "c" }), { runs: [], next_cursor: "c" });
});

// -- why a run failed ---------------------------------------------------------
//
// The detail view read one field of the detail body — `routing` — and showed a
// failed run as a red badge over four timestamps. US2 scenario 7 and SC-015 both
// need the reason, and the route has always sent it.

test("a failed run reports its stage and its error class", () => {
  const body = { status: "failed", failed_stage: "parse", error_class: "ParserCapabilityError" };
  assert.deepEqual(toFailure(body), { stage: "parse", errorClass: "ParserCapabilityError" });
});

test("a run that failed before reaching a stage has no stage, and that is a fact", () => {
  // An unresolvable schema fails a run with no stage having run. `null` is the
  // distinction the API's own model draws, not a missing value to paper over.
  const body = { status: "failed", failed_stage: null, error_class: "SchemaNotFound" };
  assert.deepEqual(toFailure(body), { stage: null, errorClass: "SchemaNotFound" });
});

test("a succeeded run reports no failure", () => {
  assert.equal(toFailure({ status: "succeeded", processing_id: "sha256:abc" }), null);
});

test("a malformed or empty body reports no failure rather than an empty label", () => {
  assert.equal(toFailure(null), null);
  assert.equal(toFailure("nope"), null);
  assert.equal(toFailure({ error_class: "" }), null);
  assert.equal(toFailure({ error_class: 7 }), null);
});

test("re-reading a page asks again for the page you are on", () => {
  // Page two, filtered, loaded.
  const paged = loaded(forward(loaded(filtered(initialListing(), "running"), PAGE)), PAGE);
  assert.equal(currentCursor(paged), "c1");

  const again = reload(paged);

  // The position is kept — this is the whole difference from `filtered`, which
  // clears the stack because a filter produces a different ordering. Re-reading
  // does not change the ordering, so the cursor still addresses it.
  assert.equal(currentCursor(again), "c1");
  assert.equal(again.status, "running");
  assert.equal(again.listing.state, "loading");
});

test("re-reading moves something, or the button does nothing", () => {
  // The fetch is keyed on the filter and the cursor, and re-reading changes
  // neither. If nothing else moves, the request is keyed on nothing new and
  // the control is decorative — which is what the first version of it was.
  const loadedState = loaded(initialListing(), PAGE);
  const again = reload(loadedState);

  assert.notEqual(again.generation, loadedState.generation);

  // And it keeps moving, so a second re-read is not mistaken for the first.
  assert.notEqual(reload(again).generation, again.generation);
});

test("re-reading is the only transition that moves it", () => {
  // Every other transition already changes the filter or the cursor, so
  // bumping this too would key a second fetch on the same page.
  const base = loaded(initialListing(), PAGE);
  assert.equal(forward(base).generation, base.generation);
  assert.equal(back(forward(base)).generation, base.generation);
  assert.equal(filtered(base, "failed").generation, base.generation);
  assert.equal(failed(base, { kind: "offline" } as never).generation, base.generation);
  assert.equal(loaded(base, PAGE).generation, base.generation);
});
