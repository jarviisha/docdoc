/**
 * T028 — the credential's whole life, which is one page long.
 *
 * SC-003a and FR-009a are measured here rather than in a browser: expiry is a
 * pure function of the session and the clock, so the bound is checked without
 * waiting fifteen minutes and without a fake timer.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  IDLE_BOUND_MS,
  asOpen,
  dropped,
  emptySession,
  expired,
  normalise,
  ready,
  touch,
  withAdministrative,
  withCredential,
} from "../../src/console/model/session.ts";

const T0 = 1_700_000_000_000;

test("a fresh session holds nothing and can do nothing", () => {
  const session = emptySession(T0);
  assert.equal(session.credential, null);
  assert.equal(session.mode, "unknown");
  assert.equal(session.administrative, false);
  assert.equal(ready(session), false);
});

test("a pasted key is trimmed of what the clipboard adds", () => {
  assert.equal(normalise("  sk-abc\n"), "sk-abc");
  const session = withCredential(emptySession(T0), "sk-abc\n", T0);
  assert.equal(session.credential, "sk-abc");
  assert.equal(ready(session), true);
});

test("an empty paste is not a credential", () => {
  const session = withCredential(emptySession(T0), "   ", T0);
  assert.equal(session.credential, null);
  assert.equal(ready(session), false);
});

test("expiry happens at the bound and not before", () => {
  const session = withCredential(emptySession(T0), "sk-abc", T0);
  assert.equal(expired(session, T0 + IDLE_BOUND_MS - 1), false);
  assert.equal(expired(session, T0 + IDLE_BOUND_MS), true);
});

test("activity postpones expiry", () => {
  const session = withCredential(emptySession(T0), "sk-abc", T0);
  const later = touch(session, T0 + IDLE_BOUND_MS - 1);
  assert.equal(expired(later, T0 + IDLE_BOUND_MS), false);
  assert.equal(expired(later, T0 + 2 * IDLE_BOUND_MS), true);
});

test("the documented bound is fifteen minutes", () => {
  // FR-009a requires the bound be documented as a number. This is that number,
  // and the test exists so the documentation and the code cannot drift.
  assert.equal(IDLE_BOUND_MS, 15 * 60 * 1000);
});

test("an open deployment never expires, because there is nothing to discard", () => {
  const session = asOpen(emptySession(T0), T0);
  assert.equal(session.mode, "open");
  assert.equal(ready(session), true);
  assert.equal(expired(session, T0 + 10 * IDLE_BOUND_MS), false);
});

test("dropping a credential drops what was learned with it", () => {
  const session = withAdministrative(withCredential(emptySession(T0), "sk-abc", T0), true);
  assert.equal(session.administrative, true);

  const after = dropped(session, T0 + 1);
  assert.equal(after.credential, null);
  assert.equal(after.administrative, false, "an area the next key may not see would still show");
  assert.equal(ready(after), false);
});
