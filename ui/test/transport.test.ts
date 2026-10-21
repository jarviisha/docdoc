/**
 * `send`'s one decision left after FR-041 moved everything else into a plan:
 * what `Content-Type` a body gets. A JSON string names itself; a file does
 * not, and must not be made to lie about its own type.
 */

import assert from "node:assert/strict";
import { afterEach, test } from "node:test";

import { send, type RequestPlan } from "../src/transport.ts";

const PLAN: RequestPlan = { method: "POST", path: "/v1/console/x", query: {}, hasBody: true };

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
