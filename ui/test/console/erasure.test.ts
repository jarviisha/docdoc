/**
 * T053, T057 — the confirmation, and the bulk path that does not exist.
 *
 * FR-030 is one comparison, and it is the only thing between a mis-click and a
 * customer's data. That is why it is in the model and why it is tested here.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import * as erasure from "../../src/console/model/erasure.ts";
import {
  armed,
  confirmationPrompt,
  intendedFor,
  outcomeNotice,
  removedFrom,
  retyped,
} from "../../src/console/model/erasure.ts";

const TENANT = { kind: "tenant" as const, id: "acme" };

test("an unconfirmed erasure is not armed", () => {
  assert.equal(armed(intendedFor(TENANT)), false);
});

test("one differing character leaves it unarmed", () => {
  for (const typed of ["acm", "acmee", "Acme", " acme", "acme ", ""]) {
    const intent = retyped(intendedFor(TENANT), typed);
    assert.equal(armed(intent), false, `"${typed}" armed an erasure of "acme"`);
  }
});

test("the exact identifier arms it", () => {
  assert.equal(armed(retyped(intendedFor(TENANT), "acme")), true);
});

test("a pasted credential in the confirmation field fails closed", () => {
  // Muscle memory: the operator pastes the key they have been pasting all
  // session. The comparison is against the target, so this cannot proceed.
  const intent = retyped(intendedFor(TENANT), "sk-live-abcdef");
  assert.equal(armed(intent), false);
});

test("the prompt names what is about to go", () => {
  assert.match(confirmationPrompt(intendedFor(TENANT)), /tenant identifier to confirm: acme/);
  assert.match(
    confirmationPrompt(intendedFor({ kind: "document", id: "sha256:aa" })),
    /document identifier to confirm/,
  );
});

test("the counts are the deployment's own", () => {
  assert.deepEqual(removedFrom({ blobs: 2, artifacts: 5, runs: 1 }), {
    blobs: 2,
    artifacts: 5,
    runs: 1,
  });
  // Nothing is invented for a body that carries no counts.
  assert.deepEqual(removedFrom(null), {});
  assert.deepEqual(removedFrom({ detail: "ok" }), {});
});

test("removing nothing is a success and says so", () => {
  // FR-032: the routes are idempotent. Presenting this as a failure would teach
  // an operator to retry an operation that already did all it was going to.
  assert.match(outcomeNotice({}), /succeeded/);
  assert.match(outcomeNotice({ blobs: 0, runs: 0 }), /Nothing was there to remove/);
  assert.match(outcomeNotice({ blobs: 3 }), /3 blobs/);
});

test("there is no bulk or multi-target erasure path at all", () => {
  // FR-033, asserted as an absence rather than left unimplemented. A model with
  // no plural entry point is a model a component cannot grow one against.
  const surface = Object.keys(erasure);
  for (const name of surface) {
    assert.doesNotMatch(name, /all|bulk|many|each|every/i, `${name} looks like a bulk path`);
  }
});
