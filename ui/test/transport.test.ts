/**
 * `send`'s one decision left after FR-041 moved everything else into a plan:
 * what `Content-Type` a body gets. A JSON string names itself; a file does
 * not, and must not be made to lie about its own type.
 */

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { send, sendForBlob, type RequestPlan } from "../src/transport.ts";

const PLAN: RequestPlan = { method: "POST", path: "/v1/console/x", query: {}, hasBody: true };
const GET_PLAN: RequestPlan = { method: "GET", path: "/v1/console/runs/r1/pages/0", query: {}, hasBody: false };

let original: typeof fetch;

afterEach(() => {
  if (original !== undefined) globalThis.fetch = original;
});

function stub(headers: Headers[]) {
  original = globalThis.fetch;
  globalThis.fetch = (async (_url: unknown, init?: RequestInit) => {
    headers.push(new Headers(init?.headers));
    return new Response("{}", { status: 200 });
  }) as typeof fetch;
}

test("a string body is sent as JSON", async () => {
  const seen: Headers[] = [];
  stub(seen);
  await send(PLAN, { body: "{}" });
  assert.equal(seen.length, 1);
  assert.equal(seen[0]?.get("Content-Type"), "application/json");
});

test("a File body carries no Content-Type this layer invented", async () => {
  const seen: Headers[] = [];
  stub(seen);
  await send(PLAN, { body: new File(["x"], "a.png", { type: "image/png" }) });
  assert.equal(seen.length, 1);
  assert.equal(seen[0]?.get("Content-Type"), null);
});

test("a plan's headers are sent alongside the console header", async () => {
  const seen: Headers[] = [];
  stub(seen);
  await send({ ...PLAN, headers: { "Idempotency-Key": "k1" } }, { body: "{}" });
  assert.equal(seen[0]?.get("Idempotency-Key"), "k1");
  assert.equal(seen[0]?.get("X-Docdoc-Console"), "1");
});

test("sendForBlob carries the console header and returns the bytes on success", async () => {
  original = globalThis.fetch;
  const seen: Headers[] = [];
  globalThis.fetch = (async (_url: unknown, init?: RequestInit) => {
    seen.push(new Headers(init?.headers));
    return new Response(new Blob([new Uint8Array([1, 2, 3])]), {
      status: 200,
      headers: { "Content-Type": "image/png" },
    });
  }) as typeof fetch;

  const result = await sendForBlob(GET_PLAN);
  assert.equal(result.ok, true);
  assert.equal(result.status, 200);
  assert.equal(seen[0]?.get("X-Docdoc-Console"), "1");
  assert.ok(result.blob instanceof Blob);
  assert.equal(result.blob?.type, "image/png");
});

test("sendForBlob reports no blob when the deployment refused the request", async () => {
  original = globalThis.fetch;
  globalThis.fetch = (async () => new Response("not found", { status: 404 })) as typeof fetch;

  const result = await sendForBlob(GET_PLAN);
  assert.equal(result.ok, false);
  assert.equal(result.status, 404);
  assert.equal(result.blob, null);
});
