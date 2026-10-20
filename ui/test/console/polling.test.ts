/**
 * How often the overview refreshes itself, and why it stops when nobody is
 * looking: a hidden tab polling a tenant's runs every five seconds is traffic
 * spent on a screen nobody reads, and `visibilitychange` is the one signal
 * that tells the console so.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { nextPoll, onVisible, POLL_MS } from "../../src/console/model/polling.ts";

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
