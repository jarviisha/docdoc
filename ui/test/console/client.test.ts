/**
 * T020 — every intent addresses an allowed path, and none carries a credential.
 *
 * The console's whole write surface is routes Milestone 10 already ships, and
 * this is where that is checked without a deployment: the allow-list is nine
 * path shapes, and a tenth appearing here is a defect rather than a feature.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  allIntents,
  isAllowed,
  requestFor,
  resultHref,
  writes,
  type Intent,
} from "../../src/console/model/client.ts";

test("every intent addresses an allowed path", () => {
  for (const intent of allIntents()) {
    const plan = requestFor(intent);
    assert.ok(isAllowed(plan), `${intent.type} addresses ${plan.path}, which is not allowed`);
  }
});

test("an unknown path is not allowed", () => {
  assert.equal(isAllowed({ method: "GET", path: "/v1/anything", query: {}, hasBody: false }), false);
  // The result endpoint is linked to and never requested (FR-022a). It is not on
  // the allow-list, which is what makes that a property rather than a habit.
  assert.equal(
    isAllowed({ method: "GET", path: "/v1/jobs/x/result", query: {}, hasBody: false }),
    false,
  );
});

test("no intent puts the credential anywhere in the request it constructs", () => {
  // FR-008. The credential is a header supplied to `send`; nothing in a plan can
  // carry it, and this fails if an intent ever grows a `key` or `token` query.
  //
  // The **query** and not the path: `/v1/admin/credentials` is a route whose
  // name contains the word, which is the first thing this assertion caught about
  // itself.
  for (const intent of allIntents()) {
    const query = new URLSearchParams(requestFor(intent).query).toString();
    assert.doesNotMatch(query, /bearer|token|key|credential|secret/i);
  }
});

test("the writes are exactly the four Milestone 10 routes", () => {
  const written = allIntents()
    .map(requestFor)
    .filter(writes)
    .map((plan) => `${plan.method} ${plan.path}`)
    .sort();

  assert.deepEqual(written, [
    "DELETE /v1/admin/credentials/c",
    "DELETE /v1/admin/documents/b",
    "DELETE /v1/admin/tenants/t",
    "POST /v1/admin/credentials",
  ]);
});

test("the run listing omits a filter nobody set", () => {
  const plan = requestFor({ type: "list-runs" });
  assert.deepEqual(plan.query, {});

  const filtered = requestFor({ type: "list-runs", status: "failed", limit: 50, cursor: "abc" });
  assert.deepEqual(filtered.query, { status: "failed", limit: "50", cursor: "abc" });
});

test("an identifier with a slash in it cannot escape its path segment", () => {
  const plan = requestFor({ type: "erase-tenant", tenantId: "a/../b" });
  assert.equal(plan.path, "/v1/admin/tenants/a%2F..%2Fb");
  assert.ok(isAllowed(plan));
});

test("the result link is a link and nothing this client fetches", () => {
  assert.equal(resultHref("p1"), "/v1/jobs/p1/result");
  const intents: Intent["type"][] = allIntents().map((intent) => intent.type);
  assert.equal(
    intents.some((type) => type.includes("result")),
    false,
  );
});
