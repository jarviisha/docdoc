/**
 * T102 (ADR-0023 §1) — the signed-in state, which the server now owns.
 *
 * There is no credential, no idle clock, and no administrative flag here any
 * more: a session is only the username the server last confirmed, and whether
 * that confirmation has happened yet. Everything this file used to compute —
 * expiry, the credential's trim, the probed scope — is the server's job now,
 * reached over the cookie `fetch` already sends.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { emptySession, ready, signedIn, signedOut } from "../../src/console/model/session.ts";

test("a fresh session has not been checked and signs in nobody", () => {
  const session = emptySession();
  assert.equal(session.username, null);
  assert.equal(session.checked, false);
  assert.equal(ready(session), false);
});

test("signing in names the operator and marks the session checked", () => {
  const session = signedIn(emptySession(), "alice");
  assert.equal(session.username, "alice");
  assert.equal(session.checked, true);
  assert.equal(ready(session), true);
});

test("signing out drops the username", () => {
  const session = signedOut(signedIn(emptySession(), "alice"));
  assert.equal(session.username, null);
  assert.equal(session.checked, true);
  assert.equal(ready(session), false);
});

test("a 401 on the first GET /session is itself an answer: checked, signed out", () => {
  const session = signedOut(emptySession());
  assert.equal(session.username, null);
  assert.equal(session.checked, true);
});
