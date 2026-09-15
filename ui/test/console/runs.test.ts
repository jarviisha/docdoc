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
