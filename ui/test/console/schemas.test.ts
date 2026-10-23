import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import {
  addField,
  changesBetween,
  constraintsFor,
  constraintText,
  documentOf,
  draftFrom,
  emptyDraft,
  groupByName,
  hasChanges,
  isDirty,
  isNumberConstraint,
  moveField,
  problemRows,
  removeField,
  rowOfProblem,
  saveLabel,
  sourceLabel,
  toSchemaDetail,
  toSchemaEntries,
  updateField,
  visibleRows,
  withConstraint,
} from "../../src/console/model/schemas.ts";

const file = JSON.parse(readFileSync(new URL("../../../schemas/invoice@2.json", import.meta.url), "utf8"));
const { version: _version, ...withoutVersion } = file;
const invoice = draftFrom(file, "p");
// line_items is field 6, its quantity child 1.
const quantity = [6, 1];

const row = (name: string, version: number) => ({
  identity: `${name}@${version}`,
  name,
  version,
  source: "file",
  field_count: 2,
  created_at: null,
  created_by: null,
  shadows_file: false,
});

test("toSchemaEntries reads rows and drops malformed ones", () => {
  const entries = toSchemaEntries({ entries: [row("a", 1), { identity: 3 }, null] });
  assert.deepEqual(entries, [
    { identity: "a@1", name: "a", version: 1, source: "file", fieldCount: 2, createdAt: null, createdBy: null, shadowsFile: false },
  ]);
  assert.deepEqual(toSchemaEntries("nope"), []);
});

test("groupByName sorts by name and versions ascending", () => {
  const groups = groupByName(toSchemaEntries({ entries: [row("b", 1), row("a", 2), row("a", 1)] }));
  assert.deepEqual(groups.map((g) => g.name), ["a", "b"]);
  assert.deepEqual(groups[0]?.versions.map((v) => v.version), [1, 2]);
  assert.equal(groups[0]?.latest.version, 2);
});

test("saveLabel names the next version, or @1 for a new name", () => {
  const groups = groupByName(toSchemaEntries({ entries: [row("invoice", 1), row("invoice", 2)] }));
  assert.equal(saveLabel(groups, "invoice"), "Save as invoice@3");
  assert.equal(saveLabel(groups, "foo"), "Save as foo@1");
});

test("toSchemaDetail reads a detail and rejects a body without a document", () => {
  const body = { identity: "x@1", source: "console", document: { name: "x" }, prompt: "p", base: null, created_at: "t", created_by: "u" };
  assert.deepEqual(toSchemaDetail(body), {
    identity: "x@1", source: "console", document: { name: "x" }, prompt: "p", base: null, createdAt: "t", createdBy: "u",
  });
  assert.equal(toSchemaDetail({ identity: "x@1", source: "console" }), null);
});

test("draftFrom fills defaults and documentOf round-trips the file minus its version", () => {
  assert.deepEqual(documentOf(invoice), { document: withoutVersion });
  const due = invoice.fields[2];
  assert.deepEqual([due?.required, due?.constraints, due?.fields], [false, {}, []]);
  assert.equal(invoice.rulesText, "[]");
});

test("documentOf rejects rules that are not a JSON array", () => {
  assert.ok("error" in documentOf({ ...invoice, rulesText: "{}" }));
  assert.ok("error" in documentOf({ ...invoice, rulesText: "[" }));
  assert.deepEqual(documentOf({ ...emptyDraft(), name: "n", rulesText: '[{"id":"r"}]' }), {
    document: { name: "n", fields: [], rules: [{ id: "r" }] },
  });
});

test("addField appends a blank string field under a parent; removeField takes it out", () => {
  const added = addField(invoice, [6]);
  assert.deepEqual(added.fields[6]?.fields[4], {
    name: "", type: "string", cardinality: "scalar", required: false, description: "", constraints: {}, fields: [],
  });
  assert.deepEqual(removeField(added, [6, 4]), invoice);
  assert.equal(addField(invoice, []).fields.length, invoice.fields.length + 1);
});

test("updateField patches by path and leaves the original alone", () => {
  const next = updateField(invoice, quantity, { name: "qty" });
  assert.equal(next.fields[6]?.fields[1]?.name, "qty");
  assert.equal(invoice.fields[6]?.fields[1]?.name, "quantity");
});

test("changing cardinality to a group clears type and constraints; to scalar clears children", () => {
  const group = updateField(invoice, [0], { cardinality: "group" });
  assert.deepEqual([group.fields[0]?.type, group.fields[0]?.constraints], [null, {}]);
  const scalar = updateField(invoice, [6], { cardinality: "scalar" });
  assert.deepEqual([scalar.fields[6]?.type, scalar.fields[6]?.fields], ["string", []]);
});

test("moveField swaps neighbours, and past either end is a no-op", () => {
  assert.deepEqual(moveField(invoice, [0], -1), invoice);
  assert.deepEqual(moveField(invoice, [invoice.fields.length - 1], 1), invoice);
  const moved = moveField(invoice, [6, 0], 1);
  assert.deepEqual(moved.fields[6]?.fields.map((f) => f.name), ["quantity", "description", "unit_price", "amount"]);
});

test("constraintsFor mirrors the server's domains", () => {
  const f = (patch: object) => constraintsFor({ ...addField(emptyDraft(), []).fields[0]!, ...patch });
  assert.deepEqual(f({ type: "string" }), ["enum", "const", "pattern", "min_length", "max_length"]);
  assert.deepEqual(f({ type: "integer" }), ["enum", "const", "minimum", "maximum", "multiple_of"]);
  assert.deepEqual(f({ type: "date" }), ["enum", "const", "minimum", "maximum"]);
  assert.deepEqual(f({ type: "boolean" }), ["enum", "const"]);
  assert.deepEqual(f({ cardinality: "repeating_group", type: null }), ["min_length", "max_length"]);
  assert.deepEqual(f({ cardinality: "group", type: null }), []);
});

test("changesBetween keys changes by dotted field path", () => {
  const edited = updateField(invoice, quantity, { type: "integer" });
  assert.deepEqual(changesBetween(invoice, edited), {
    added: [], removed: [], changed: ["line_items.quantity"], orderChanged: false, promptChanged: false, rulesChanged: false,
  });
  const grown = { ...addField(invoice, [6]), prompt: "q", rulesText: '[{"id":"r"}]' };
  const diff = changesBetween(invoice, removeField(grown, [0]));
  assert.deepEqual([diff.added, diff.removed], [["line_items."], ["invoice_number"]]);
  assert.deepEqual([diff.promptChanged, diff.rulesChanged], [true, true]);
});

test("hasChanges sees a reorder-only edit, which lists no path", () => {
  assert.equal(hasChanges(changesBetween(invoice, invoice)), false);
  const moved = moveField(invoice, [0], 1);
  const diff = changesBetween(invoice, moved);
  assert.deepEqual([diff.added, diff.removed, diff.changed], [[], [], []]);
  assert.equal(diff.orderChanged, true);
  assert.equal(hasChanges(diff), true);
  assert.equal(hasChanges(changesBetween(invoice, { ...invoice, prompt: "x" })), true);
});

test("isDirty is false for an untouched draft and true after an edit", () => {
  assert.equal(isDirty(invoice, draftFrom(file, "p")), false);
  assert.equal(isDirty(invoice, updateField(invoice, [0], { required: false })), true);
});

test("rowOfProblem maps index-form and dotted-name problem paths to tree paths", () => {
  assert.deepEqual(rowOfProblem(invoice, "fields.6.fields.1.constraints"), [6, 1]);
  assert.deepEqual(rowOfProblem(invoice, "fields.3"), [3]);
  assert.deepEqual(rowOfProblem(invoice, "line_items.quantity"), [6, 1]);
  assert.deepEqual(rowOfProblem(invoice, "supplier"), [5]);
  assert.equal(rowOfProblem(invoice, "line_items.nope"), null);
  assert.equal(rowOfProblem(invoice, "name"), null);
  assert.equal(rowOfProblem(invoice, "<root>"), null);
});

test("sourceLabel says file, or console with when and who", () => {
  assert.equal(sourceLabel({ source: "file", createdAt: null, createdBy: null }), "file");
  const label = sourceLabel({ source: "console", createdAt: "2026-10-23T10:00:00Z", createdBy: "ops" });
  assert.match(label, /^console · .*2026.* · ops$/);
  assert.equal(sourceLabel({ source: "console", createdAt: null, createdBy: null }), "console · — · —");
});

test("visibleRows shows top-level fields and opens only expanded groups", () => {
  const closed = visibleRows(invoice.fields, new Set());
  assert.equal(closed.length, invoice.fields.length);
  assert.deepEqual(closed[6], { field: invoice.fields[6], path: [6], key: "6", depth: 0, expandable: true });
  assert.equal(closed[0]?.expandable, false);
  const open = visibleRows(invoice.fields, new Set(["6"]));
  assert.deepEqual(open.slice(7, 9).map((r) => [r.key, r.depth, r.field.name]), [["6.0", 1, "description"], ["6.1", 1, "quantity"]]);
  assert.equal(open.length, invoice.fields.length + 4);
});

test("withConstraint parses typed values, comma enums, and drops a cleared key", () => {
  const f = (patch: object) => ({ ...addField(emptyDraft(), []).fields[0]!, ...patch });
  const int = f({ type: "integer", constraints: { minimum: 1 } });
  assert.deepEqual(withConstraint(int, "enum", "1, 2,,3"), { minimum: 1, enum: [1, 2, 3] });
  assert.deepEqual(withConstraint(int, "minimum", ""), {});
  assert.deepEqual(withConstraint(int, "maximum", "9"), { minimum: 1, maximum: 9 });
  assert.deepEqual(withConstraint(f({ type: "string" }), "enum", "EUR, USD"), { enum: ["EUR", "USD"] });
  assert.deepEqual(withConstraint(f({ type: "string" }), "min_length", "2"), { min_length: 2 });
  assert.deepEqual(withConstraint(f({ type: "boolean" }), "const", "true"), { const: true });
  assert.deepEqual(withConstraint(f({ type: "date" }), "minimum", "2020-01-01"), { minimum: "2020-01-01" });
  // Not a number: kept as typed, so the server names the problem.
  assert.deepEqual(withConstraint(int, "const", "x"), { minimum: 1, const: "x" });
});

test("constraintText and isNumberConstraint", () => {
  assert.equal(constraintText(["EUR", "USD"]), "EUR, USD");
  assert.equal(constraintText(undefined), "");
  assert.equal(constraintText(3), "3");
  const f = (patch: object) => ({ ...addField(emptyDraft(), []).fields[0]!, ...patch });
  assert.equal(isNumberConstraint(f({ type: "string" }), "max_length"), true);
  assert.equal(isNumberConstraint(f({ type: "integer" }), "minimum"), true);
  assert.equal(isNumberConstraint(f({ type: "date" }), "minimum"), false);
  assert.equal(isNumberConstraint(f({ type: "integer" }), "enum"), false);
});

test("problemRows keys every row a problem names", () => {
  const rows = problemRows(invoice, [
    { path: "fields.6.fields.1.constraints", message: "a" },
    { path: "supplier", message: "b" },
    { path: "<root>", message: "c" },
  ]);
  assert.deepEqual([...rows].sort(), ["5", "6.1"]);
});
