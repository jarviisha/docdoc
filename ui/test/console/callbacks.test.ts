/**
 * Callbacks: register and revoke a tenant's delivery endpoint. The secret is
 * never returned or logged on any route (FR-066) — `toCallbacks` is the
 * model's own guarantee that a listing carries none either, whatever extra
 * keys a row happened to answer with.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  canRegister,
  canRevoke,
  choosing,
  listFailedFor,
  listProblem,
  listedFor,
  registerBody,
  toCallbacks,
  unavailableNote,
} from "../../src/console/model/callbacks.ts";

const ROW = {
  callback_id: "c1",
  tenant_id: "a",
  url: "https://hooks.example.com/a",
  created_at: "2026-10-21T09:00:00+00:00",
};

test("cannot revoke while sending", () => {
  assert.equal(canRevoke(true), false);
  assert.equal(canRevoke(false), true);
});

test("register needs both fields, trimmed, and nothing already sending", () => {
  assert.equal(canRegister(" https://example.com ", " s3cr3t ", false), true);
  assert.equal(canRegister("   ", "s3cr3t", false), false);
  assert.equal(canRegister("https://example.com", "   ", false), false);
  assert.equal(canRegister("https://example.com", "s3cr3t", true), false);
});

test("register body trims both fields", () => {
  assert.equal(
    registerBody(" https://example.com ", " s3cr3t "),
    JSON.stringify({ url: "https://example.com", secret: "s3cr3t" }),
  );
});

test("callbacks list never carries a secret", () => {
  const records = toCallbacks({
    callbacks: [
      {
        callback_id: "cb1",
        tenant_id: "acme",
        url: "https://example.com/hook",
        created_at: "2026-09-11T00:00:00Z",
        secret: "s3cr3t",
        secret_digest: "abc123",
      },
    ],
  });
  assert.equal(records.length, 1);
  assert.deepEqual(Object.keys(records[0] as object).sort(), [
    "callback_id",
    "created_at",
    "tenant_id",
    "url",
  ]);
  assert.deepEqual(records[0], {
    callback_id: "cb1",
    tenant_id: "acme",
    url: "https://example.com/hook",
    created_at: "2026-09-11T00:00:00Z",
  });
});

test("a row missing a required field is dropped, not guessed at", () => {
  assert.deepEqual(toCallbacks({ callbacks: [{ callback_id: "c1" }] }), []);
  assert.deepEqual(toCallbacks({}), []);
  assert.deepEqual(toCallbacks(null), []);
});

test("a list answer for the tenant shown fills the table", () => {
  const listing = listedFor(choosing("a"), "a", [ROW]);
  assert.deepEqual(listing.callbacks, [ROW]);
  assert.equal(listProblem(listing), null);
  assert.equal(unavailableNote(listing), null);
});

test("a late answer for a tenant no longer shown is ignored, success or failure", () => {
  const onB = choosing("b");
  assert.deepEqual(listedFor(onB, "a", [ROW]), onB);
  assert.deepEqual(listFailedFor(onB, "a", { kind: "absent" }), onB);
  assert.deepEqual(
    listFailedFor(onB, "a", { kind: "unavailable", message: "no callbacks" }),
    onB,
  );
});

test("choosing a tenant drops the previous tenant's rows and failure", () => {
  const failed = listFailedFor(choosing("a"), "a", { kind: "absent" });
  assert.deepEqual(choosing("b"), { tenant: "b", callbacks: null, failure: null });
  assert.notDeepEqual(failed, choosing("b"));
});

test("a 503 is said in place of the table, not as a problem", () => {
  const listing = listFailedFor(choosing("a"), "a", { kind: "unavailable", message: "no callbacks" });
  assert.equal(unavailableNote(listing), "no callbacks");
  assert.equal(listProblem(listing), null);
  assert.equal(listing.callbacks, null);
});

test("any other list failure is a problem, said in the deployment's words", () => {
  const listing = listFailedFor(choosing("a"), "a", { kind: "absent" });
  assert.equal(listProblem(listing), "this deployment does not offer that.");
  assert.equal(unavailableNote(listing), null);
});
