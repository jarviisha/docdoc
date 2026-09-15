/**
 * T060 — "no destination registered" is an answer, not an empty table.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { NOT_OFFERED, summarise, toDelivery } from "../../src/console/model/delivery.ts";

test("a run with no destination says so", () => {
  const delivery = toDelivery({ run_id: "r1" });
  assert.equal(delivery.state, "none");
  assert.match(summarise(delivery), /No destination was registered/);
});

test("attempts in flight are distinguishable from a delivery at rest", () => {
  const attempting = toDelivery({
    callback_id: "cb1",
    status: "pending",
    attempts: [{ attempted_at: "t", status: 500, outcome: "refused" }],
  });
  assert.equal(attempting.state, "attempting");
  assert.match(summarise(attempting), /still being retried/);

  const exhausted = toDelivery({
    callback_id: "cb1",
    status: "exhausted",
    attempts: [{ attempted_at: "t", status: 500, outcome: "refused" }],
  });
  assert.equal(exhausted.state, "at-rest");
  assert.match(summarise(exhausted), /Not delivered/);
});

test("a delivered run says so with its attempt count", () => {
  const delivered = toDelivery({
    callback_id: "cb1",
    status: "delivered",
    attempts: [
      { attempted_at: "t1", status: 500, outcome: "refused" },
      { attempted_at: "t2", status: 200, outcome: "accepted" },
    ],
  });
  assert.match(summarise(delivered), /Delivered after 2/);
});

test("no shape of body produces a field that could hold a secret", () => {
  const delivery = toDelivery({
    callback_id: "cb1",
    status: "delivered",
    attempts: [{ attempted_at: "t", status: 200, outcome: "accepted" }],
    secret: "s3cr3t",
    signing_key: "also-secret",
  });

  assert.doesNotMatch(JSON.stringify(delivery), /s3cr3t|also-secret/);
});

test("what is deliberately absent is recorded where somebody would add it", () => {
  assert.deepEqual([...NOT_OFFERED], ["re-delivery", "cancellation", "destination editing"]);
});
