import assert from "node:assert/strict";
import { test } from "node:test";

import { parseQuery, qualitySearch, runsSearch, stringifyQuery, tenantSearch } from "../../src/console/model/search.ts";

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

test("query parsing keeps every value a string, even ones JSON would reinterpret", () => {
  assert.deepEqual(parseQuery("?tenant=123"), { tenant: "123" });
  assert.deepEqual(runsSearch(parseQuery("?tenant=null&status=failed")), { tenant: "null", status: "failed" });
  for (const id of ["2024", "-5", "1e5", "true", "false"]) assert.deepEqual(tenantSearch(parseQuery(`?tenant=${id}`)), { tenant: id });
});

test("query stringifying writes plain strings and omits the empty", () => {
  assert.equal(stringifyQuery({ tenant: "123" }), "?tenant=123");
  assert.equal(stringifyQuery({}), "");
  assert.equal(stringifyQuery({ tenant: undefined }), "");
  assert.equal(stringifyQuery({ tenant: "" }), "");
  assert.deepEqual(parseQuery(stringifyQuery({ tenant: "a b" })), { tenant: "a b" });
});
