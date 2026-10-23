import assert from "node:assert/strict";
import { test } from "node:test";

import {
  adapterUpdate,
  editable,
  isOverride,
  keyDisplay,
  movePriority,
  SOURCE_LABEL,
  testBody,
  toProvidersView,
  withAdapter,
  withoutAdapter,
} from "../../src/console/model/providers.ts";

const adapter = (id: string, over: Record<string, unknown> = {}) => ({
  id,
  test_only: id === "echo",
  model: { value: "m", source: "default" },
  key: { last4: null, source: "default" },
  status: { usable: true, reason: null },
  updated_at: null,
  updated_by: null,
  ...over,
});

const body = {
  scope: null,
  selected: "b",
  secrets_configured: true,
  priority: { value: ["b", "a"], source: "env" },
  adapters: [
    adapter("a"),
    adapter("c", { key: { last4: "1234", source: "tenant" }, updated_at: "t", updated_by: "u" }),
    adapter("b", { status: { usable: false, reason: "no key" } }),
    adapter("echo"),
  ],
};

test("a body becomes a view, rows in priority order then by id", () => {
  const v = toProvidersView(body)!;
  assert.equal(v.scope, null);
  assert.equal(v.selected, "b");
  assert.equal(v.secretsConfigured, true);
  assert.deepEqual(v.priority, ["b", "a"]);
  assert.equal(v.prioritySource, "env");
  assert.deepEqual(v.rows.map((r) => r.id), ["b", "a", "c", "echo"]);
  assert.deepEqual(v.rows[0], {
    id: "b",
    testOnly: false,
    model: "m",
    modelSource: "default",
    keyLast4: null,
    keySource: "default",
    usable: false,
    reason: "no key",
    updatedAt: null,
    updatedBy: null,
  });
  assert.equal(v.rows[2]!.keyLast4, "1234");
});

test("a tenant scope is kept; junk is null", () => {
  assert.equal(toProvidersView({ ...body, scope: "acme" })!.scope, "acme");
  assert.equal(toProvidersView(null), null);
  assert.equal(toProvidersView({}), null);
  assert.equal(toProvidersView({ ...body, priority: { value: [], source: "nope" } }), null);
});

test("labels, key display, editable", () => {
  assert.equal(SOURCE_LABEL.tenant, "this tenant");
  assert.equal(SOURCE_LABEL.default, "deployment default");
  assert.equal(SOURCE_LABEL.env, "environment");
  assert.equal(SOURCE_LABEL.shipped, "built-in");
  const v = toProvidersView(body)!;
  assert.equal(keyDisplay(v.rows[2]!), "••••1234");
  assert.equal(keyDisplay(v.rows[0]!), "not set");
  assert.equal(editable(v.rows[0]!), true);
  assert.equal(editable(v.rows[3]!), false);
});

test("movePriority swaps and is a no-op at the ends", () => {
  assert.deepEqual(movePriority(["a", "b", "c"], "b", -1), ["b", "a", "c"]);
  assert.deepEqual(movePriority(["a", "b", "c"], "b", 1), ["a", "c", "b"]);
  assert.deepEqual(movePriority(["a", "b"], "a", -1), ["a", "b"]);
  assert.deepEqual(movePriority(["a", "b"], "b", 1), ["a", "b"]);
  assert.deepEqual(movePriority(["a", "b"], "x", 1), ["a", "b"]);
});

test("withAdapter appends once; withoutAdapter keeps the last entry", () => {
  assert.deepEqual(withAdapter(["a"], "b"), ["a", "b"]);
  assert.deepEqual(withAdapter(["a"], "a"), ["a"]);
  assert.deepEqual(withoutAdapter(["a", "b"], "a"), ["b"]);
  assert.deepEqual(withoutAdapter(["a"], "a"), ["a"]);
});

test("adapterUpdate maps the form to a body", () => {
  assert.deepEqual(adapterUpdate({ model: "", apiKey: "", clearKey: false }), { model: null });
  assert.deepEqual(adapterUpdate({ model: " m ", apiKey: "", clearKey: false }), { model: "m" });
  assert.deepEqual(adapterUpdate({ model: "m", apiKey: "k", clearKey: false }), { model: "m", api_key: "k" });
  assert.deepEqual(adapterUpdate({ model: "m", apiKey: "", clearKey: true }), { model: "m", api_key: null });
});

test("testBody omits blank fields", () => {
  assert.deepEqual(testBody({ model: "", apiKey: " " }), {});
  assert.deepEqual(testBody({ model: "m", apiKey: "k" }), { model: "m", api_key: "k" });
});

test("isOverride is true when any source is tenant", () => {
  assert.equal(isOverride(toProvidersView(body)!), true);
  const plain = { ...body, adapters: [adapter("a")] };
  assert.equal(isOverride(toProvidersView(plain)!), false);
  assert.equal(isOverride(toProvidersView({ ...plain, priority: { value: ["a"], source: "tenant" } })!), true);
});
