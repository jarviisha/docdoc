/**
 * T102 (ADR-0023 §3) — every intent addresses `/v1/console`, and nothing else.
 *
 * The console used to speak to `/v1/runs`, `/v1/admin/credentials`, and
 * `/v1/admin/tenants` with a Bearer key resolving each call to one tenant.
 * ADR-0023 §3 replaces that with its own route family, answering only to the
 * session cookie: every intent now lives under `/v1/console`, and a tenant is
 * a parameter rather than something a key resolves to.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import { allIntents, isAllowed, requestFor } from "../../src/console/model/client.ts";

test("every intent addresses the console API and nothing else", () => {
  for (const intent of allIntents()) {
    const plan = requestFor(intent);
    assert.ok(plan.path.startsWith("/v1/console/"), plan.path);
    assert.ok(isAllowed(plan));
  }
});

test("sign-in is a POST with a body; who-am-i a GET; sign-out a DELETE", () => {
  assert.deepEqual(requestFor({ type: "sign-in" }), {
    method: "POST",
    path: "/v1/console/session",
    query: {},
    hasBody: true,
  });
  assert.equal(requestFor({ type: "who-am-i" }).method, "GET");
  assert.equal(requestFor({ type: "sign-out" }).method, "DELETE");
});

test("document erasure names its tenant in the path", () => {
  assert.equal(
    requestFor({ type: "erase-document", tenantId: "acme", blobId: "sha256:x" }).path,
    "/v1/console/tenants/acme/documents/sha256%3Ax",
  );
});

test("the run list carries the tenant filter only when set", () => {
  assert.deepEqual(requestFor({ type: "list-runs" }).query, {});
  assert.deepEqual(requestFor({ type: "list-runs", tenant: "acme" }).query, { tenant: "acme" });
});

test("the run list carries the kind filter only when set", () => {
  assert.deepEqual(requestFor({ type: "list-runs" }).query, {});
  assert.deepEqual(requestFor({ type: "list-runs", kind: "ocr" }).query, { kind: "ocr" });
});

test("overview is a GET under the console API", () => {
  assert.deepEqual(requestFor({ type: "overview" }), {
    method: "GET",
    path: "/v1/console/overview",
    query: {},
    hasBody: false,
  });
});

test("cancel-run is a DELETE under the console API", () => {
  assert.deepEqual(requestFor({ type: "cancel-run", runId: "r 1" }), {
    method: "DELETE",
    path: "/v1/console/runs/r%201",
    query: {},
    hasBody: false,
  });
});

test("an unknown path is not allowed", () => {
  assert.equal(isAllowed({ method: "GET", path: "/v1/anything", query: {}, hasBody: false }), false);
  assert.equal(isAllowed({ method: "GET", path: "/v1/runs", query: {}, hasBody: false }), false);
});

test("the run listing carries every filter that was set", () => {
  const plan = requestFor({
    type: "list-runs",
    tenant: "acme",
    status: "failed",
    kind: "ocr",
    limit: 50,
    cursor: "abc",
  });
  assert.deepEqual(plan.query, {
    tenant: "acme",
    status: "failed",
    kind: "ocr",
    limit: "50",
    cursor: "abc",
  });
});

test("run reads are scoped by run id, not by tenant", () => {
  assert.equal(requestFor({ type: "run-detail", runId: "r" }).path, "/v1/console/runs/r");
  assert.equal(requestFor({ type: "run-delivery", runId: "r" }).path, "/v1/console/runs/r/delivery");
  assert.equal(requestFor({ type: "run-result", runId: "r" }).path, "/v1/console/runs/r/result");
});

test("run-page addresses the console page route", () => {
  assert.deepEqual(requestFor({ type: "run-page", runId: "r1", page: 2 }), {
    method: "GET",
    path: "/v1/console/runs/r1/pages/2",
    query: {},
    hasBody: false,
  });
});

test("credential listing carries the tenant as a query parameter", () => {
  assert.deepEqual(requestFor({ type: "list-credentials", tenant: "acme" }).query, {
    tenant: "acme",
  });
});

test("tenant erasure names the tenant in the path", () => {
  assert.equal(requestFor({ type: "erase-tenant", tenantId: "acme" }).path, "/v1/console/tenants/acme");
});

test("an identifier with a slash in it cannot escape its path segment", () => {
  const plan = requestFor({ type: "erase-tenant", tenantId: "a/../b" });
  assert.equal(plan.path, "/v1/console/tenants/a%2F..%2Fb");
  assert.ok(isAllowed(plan));
});

test("the writes are exactly the session, run, credential, erasure, and OCR submission actions", () => {
  const written = allIntents()
    .map(requestFor)
    .filter((plan) => plan.method !== "GET")
    .map((plan) => `${plan.method} ${plan.path}`)
    .sort();

  assert.deepEqual(written, [
    "DELETE /v1/console/credentials/c",
    "DELETE /v1/console/runs/r",
    "DELETE /v1/console/session",
    "DELETE /v1/console/tenants/t",
    "DELETE /v1/console/tenants/t/documents/b",
    "POST /v1/console/credentials",
    "POST /v1/console/session",
    "POST /v1/console/tenants/t/ocr",
  ]);
});

test("the tenant and parser lists are GETs under the console API", () => {
  assert.deepEqual(requestFor({ type: "list-tenants" }), {
    method: "GET",
    path: "/v1/console/tenants",
    query: {},
    hasBody: false,
  });
  assert.deepEqual(requestFor({ type: "list-parsers" }), {
    method: "GET",
    path: "/v1/console/parsers",
    query: {},
    hasBody: false,
  });
});

test("submitting OCR posts to the tenant's own path, with a body", () => {
  const plan = requestFor({ type: "submit-ocr", tenantId: "acme" });
  assert.deepEqual(plan, {
    method: "POST",
    path: "/v1/console/tenants/acme/ocr",
    query: {},
    hasBody: true,
  });
});

test("submitting OCR carries the parser and language only when set", () => {
  assert.deepEqual(requestFor({ type: "submit-ocr", tenantId: "acme" }).query, {});
  assert.deepEqual(requestFor({ type: "submit-ocr", tenantId: "acme", parser: "paddle-vl" }).query, {
    parser: "paddle-vl",
  });
  assert.deepEqual(
    requestFor({ type: "submit-ocr", tenantId: "acme", parser: "paddle-vl", language: "vi" }).query,
    { parser: "paddle-vl", language: "vi" },
  );
});

test("an OCR submission names its tenant in the path, encoded like any other", () => {
  assert.equal(
    requestFor({ type: "submit-ocr", tenantId: "a/../b" }).path,
    "/v1/console/tenants/a%2F..%2Fb/ocr",
  );
});
