import assert from "node:assert/strict";
import { test } from "node:test";

import { qualitySearch, runsSearch, tenantSearch } from "../../src/console/model/search.ts";

test("runs search keeps valid filters", () => {
  assert.deepEqual(runsSearch({ tenant: " acme ", status: "failed", kind: "ocr" }), { tenant: "acme", status: "failed", kind: "ocr" });
});

test("runs search drops bad values and unknown keys", () => {
  assert.deepEqual(runsSearch({ tenant: "  ", status: "bogus", kind: "", page: 3 }), {});
  assert.deepEqual(runsSearch({ tenant: ["a", "b"], status: 5 }), {});
});

test("tenant search", () => {
  assert.deepEqual(tenantSearch({ tenant: "acme" }), { tenant: "acme" });
  assert.deepEqual(tenantSearch({}), {});
});

test("quality search", () => {
  assert.deepEqual(qualitySearch({ tenant: "acme", schema: "invoice@1", evaluation: "ev1", x: 1 }), { tenant: "acme", schema: "invoice@1", evaluation: "ev1" });
});
