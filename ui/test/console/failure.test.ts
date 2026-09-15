/**
 * T075 — the four things that can go wrong, told apart.
 *
 * The `removed` case is the one convergence found missing: a `410` arrived as an
 * ordinary reported failure, so the two fields FR-023 names were parsed by
 * nobody and shown by nobody, while a comment claimed otherwise.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { describe, failureOf, failureOfThrown } from "../../src/console/model/failure.ts";
import { TransportError } from "../../src/transport.ts";

const TOMBSTONE = {
  error: "run_erased",
  run_id: "a4e03746-11be-4e79-ab27-f079481fed52",
  deleted_at: "2026-09-11T09:17:00+00:00",
  policy: "retention",
};

test("a 410 is a removal, and carries when and under which policy", () => {
  const failure = failureOf({ ok: false, status: 410, body: TOMBSTONE });

  assert.equal(failure.kind, "removed");
  assert.deepEqual(failure, {
    kind: "removed",
    deletedAt: "2026-09-11T09:17:00+00:00",
    policy: "retention",
  });
});

test("the sentence names both fields and nothing about what it held", () => {
  const sentence = describe(failureOf({ ok: false, status: 410, body: TOMBSTONE }));

  assert.match(sentence, /2026-09-11/);
  assert.match(sentence, /retention/);
  // A tombstone carries four fields precisely so that being told about one
  // discloses nothing about its contents.
  assert.doesNotMatch(sentence, /invoice|sha256|blob/i);
});

test("a 410 whose body is not a tombstone is still a removal", () => {
  const failure = failureOf({ ok: false, status: 410, body: { error: "gone" } });

  assert.equal(failure.kind, "removed");
  assert.match(describe(failure), /removed/);
});

test("a 401 is a refused credential, not a reported failure", () => {
  const failure = failureOf({ ok: false, status: 401, body: {} });

  assert.equal(failure.kind, "unauthenticated");
  assert.match(describe(failure), /not accepted/);
});

test("a 404 is absence, because that is what an administrative route says", () => {
  // ADR-0016 §6: a principal without the scope is told the area does not exist,
  // and the console hides it rather than explaining it.
  assert.deepEqual(failureOf({ ok: false, status: 404, body: {} }), { kind: "absent" });
});

test("anything else is reported, in the deployment's own words", () => {
  const failure = failureOf({
    ok: false,
    status: 422,
    body: { error: { class: "InvalidRunStatus", message: "status must be one of …" } },
  });

  assert.equal(failure.kind, "reported");
  assert.match(describe(failure), /status must be one of/);
});

test("a lost request is never reported as the deployment's failure", () => {
  const failure = failureOfThrown(new TransportError("the deployment answered 504"));

  assert.equal(failure.kind, "transport");
  assert.match(describe(failure), /504/);
});

test("no classification invents a message the deployment did not send", () => {
  const failure = failureOf({ ok: false, status: 500, body: null });

  assert.equal(failure.kind, "reported");
  assert.match(describe(failure), /gave no message/);
});
