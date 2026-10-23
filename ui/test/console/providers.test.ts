import assert from "node:assert/strict";
import { test } from "node:test";

import {
  adapterUpdate,
  canSave,
  editable,
  isOverride,
  keyDisplay,
  keyPlaceholder,
  keySaved,
  movePriority,
  savedModel,
  SOURCE_LABEL,
  statusOf,
  testBody,
  toProvidersView,
  toTestResult,
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

test("statusOf: in use is the selected row, else usable or unavailable with the reason", () => {
  const view = toProvidersView({ ...body, selected: null })!;
  const [b, a] = view.rows;
  assert.deepEqual(statusOf(view, a!), { label: "usable", variant: "neutral", reason: null });
  assert.deepEqual(statusOf(view, b!), { label: "unavailable", variant: "warning", reason: "no key" });
  const selectedUsable = toProvidersView({ ...body, selected: "a" })!;
  assert.equal(statusOf(selectedUsable, selectedUsable.rows.find((r) => r.id === "a")!).label, "in use");
});

test("the form starts from what this scope saved, never an inherited value", () => {
  const atDefault = toProvidersView(body)!;
  const a = atDefault.rows.find((r) => r.id === "a")!;
  const c = atDefault.rows.find((r) => r.id === "c")!;
  assert.equal(savedModel(atDefault, a), "m");
  assert.equal(keySaved(atDefault, c), false);
  const atTenant = toProvidersView({ ...body, scope: "acme" })!;
  const ta = atTenant.rows.find((r) => r.id === "a")!;
  const tc = atTenant.rows.find((r) => r.id === "c")!;
  assert.equal(savedModel(atTenant, ta), "");
  assert.equal(keySaved(atTenant, tc), true);
  assert.equal(keySaved(atTenant, ta), false);
});

test("keyPlaceholder says what an empty input keeps", () => {
  const atTenant = toProvidersView({ ...body, scope: "acme" })!;
  const row = (id: string) => atTenant.rows.find((r) => r.id === id)!;
  assert.equal(keyPlaceholder(atTenant, row("c")), "••••1234 saved — type to replace");
  const inherited = toProvidersView({
    ...body,
    scope: "acme",
    adapters: [adapter("a", { key: { last4: "9999", source: "env" } })],
  })!;
  assert.equal(keyPlaceholder(inherited, inherited.rows[0]!), "••••9999 from environment — type to override");
  assert.equal(keyPlaceholder(atTenant, row("a")), "not set — type to save");
});

test("canSave: a typed key needs the secrets key; never while busy", () => {
  const form = { model: "", apiKey: "", clearKey: false };
  assert.equal(canSave(form, false, false), true);
  assert.equal(canSave({ ...form, apiKey: "k" }, false, false), false);
  assert.equal(canSave({ ...form, apiKey: "k" }, true, false), true);
  assert.equal(canSave({ ...form, apiKey: "k", clearKey: true }, false, false), true);
  assert.equal(canSave(form, true, true), false);
});

test("toTestResult reads ok and message; junk is null", () => {
  assert.deepEqual(toTestResult({ ok: true, message: "connected" }), { ok: true, message: "connected" });
  assert.deepEqual(toTestResult({ ok: false, message: "bad key" }), { ok: false, message: "bad key" });
  assert.equal(toTestResult({ ok: "yes" }), null);
  assert.equal(toTestResult(null), null);
});

test("a checked Remove saved key wins over a typed key, in the body and in canSave", () => {
  const form = { model: "", apiKey: "typed", clearKey: true };
  assert.deepEqual(adapterUpdate(form), { model: null, api_key: null });
  assert.equal(canSave(form, false, false), true);
});
