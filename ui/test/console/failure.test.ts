/**
 * T102 (ADR-0023 §1) — what went wrong, told apart.
 *
 * A session now fails in three new ways a pasted key never did: the run-state
 * database is absent (`ConsoleUnavailable`, 503), the session was never opened
 * or has expired (`ConsoleSessionRequired`, 401, now `unauthenticated`), and a
 * login itself was refused (`LoginRefused`, also 401, but a wrong password is
 * not the same fact as a stale session and must not be told as one).
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { describe, failureOf, failureOfThrown } from "../../src/console/model/failure.ts";
import { TransportError } from "../../src/transport.ts";

test("a refusal signs the operator out with a notice", () => {
  const f = failureOf({
    ok: false,
    status: 401,
    body: { error: { class: "ConsoleSessionRequired", message: "sign in to the console" } },
  });
  assert.equal(f.kind, "unauthenticated");
});

test("unavailable is not a wrong password", () => {
  const f = failureOf({
    ok: false,
    status: 503,
    body: {
      error: {
        class: "ConsoleUnavailable",
        message: "the console needs a run-state database; …",
      },
    },
  });
  assert.equal(f.kind, "unavailable");
  assert.match(describe(f), /run-state database/);
});

test("a wrong username or password is its own kind, not a stale session", () => {
  const f = failureOf({
    ok: false,
    status: 401,
    body: {
      error: {
        class: "LoginRefused",
        message: "wrong username or password, or the account is locked",
      },
    },
  });
  assert.equal(f.kind, "refused");
  assert.match(describe(f), /wrong username or password/);
});

test("a 403 for a missing console header is still unauthenticated", () => {
  const f = failureOf({
    ok: false,
    status: 403,
    body: {
      error: {
        class: "ConsoleHeaderMissing",
        message: "a console request that changes anything must carry X-Docdoc-Console: 1",
      },
    },
  });
  assert.equal(f.kind, "unauthenticated");
});

test("a 404 is absence, unrelated to a stale session", () => {
  assert.deepEqual(failureOf({ ok: false, status: 404, body: {} }), { kind: "absent" });
});

test("a 410 is a removal, and carries when and under which policy", () => {
  const failure = failureOf({
    ok: false,
    status: 410,
    body: { deleted_at: "2026-09-11T09:17:00+00:00", policy: "retention" },
  });
  assert.deepEqual(failure, {
    kind: "removed",
    deletedAt: "2026-09-11T09:17:00+00:00",
    policy: "retention",
  });
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
