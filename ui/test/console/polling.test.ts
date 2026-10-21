/**
 * How often the overview refreshes itself, and why it stops when nobody is
 * looking: a hidden tab polling a tenant's runs every five seconds is traffic
 * spent on a screen nobody reads, and `visibilitychange` is the one signal
 * that tells the console so.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { isNewer, nextPoll, onVisible, POLL_MS, startsNow } from "../../src/console/model/polling.ts";

test("polling pauses while hidden", () => {
  assert.equal(nextPoll(true), null);
  assert.equal(nextPoll(false), POLL_MS);
});

test("the poll interval is five seconds", () => {
  assert.equal(POLL_MS, 5000);
});

test("becoming visible again means refresh now", () => {
  assert.equal(onVisible(true, false), true);
});

test("staying visible, or staying hidden, asks for nothing extra", () => {
  assert.equal(onVisible(false, false), false);
  assert.equal(onVisible(true, true), false);
  assert.equal(onVisible(false, true), false);
});

test("a poll starts only when none is in flight", () => {
  assert.equal(startsNow(false), true);
  assert.equal(startsNow(true), false, "a slow request is never overlapped by a second");
});

test("only an answer newer than the shown one is applied", () => {
  assert.equal(isNewer(1, null), true, "the first answer always lands");
  assert.equal(isNewer(3, 2), true);
  assert.equal(isNewer(2, 3), false, "an answer that arrives late, out of order, is dropped");
  assert.equal(isNewer(3, 3), false);
});
