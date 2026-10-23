import assert from "node:assert/strict";
import { test } from "node:test";

import { checkBody, type FieldRow, rowsToCheck, toFieldRows } from "../../src/console/model/fields.ts";

const RESULT = {
  extraction: {
    values: {
      invoice_no: { field_path: "invoice_no", value: "A-1", present: true },
      total: { field_path: "total", value: 12.5, present: true },
      note: { field_path: "note", value: null, present: false },
      line_items: [{ amount: { field_path: "line_items[0].amount", value: 3, present: true } }],
    },
    provenance: { document_id: "blob1" },
  },
  grounding: {
    outcomes: {
      invoice_no: { field_path: "invoice_no", status: "exact", pages: [0], geometry: [[0, [0.1, 0.2, 0.3, 0.4]]] },
      total: { field_path: "total", status: "ungrounded", pages: [], geometry: null },
    },
  },
};

const base = { report_id: "p", document_id: "blob1", field_path: "total", predicted_value: 12.5, location: null, reason: "r", annotator: "a", corrected_value: null, corrected_absence: false };
const WRONG_OLD_TOTAL = { ...base, confirmed: false, corrected_value: "13", timestamp: "2026-01-01T00:00:00Z" };
const CONFIRMED_NEW_TOTAL = { ...base, confirmed: true, timestamp: "2026-02-01T00:00:00Z" };

test("rows follow the extraction's field order, nested fields included", () => {
  const rows = toFieldRows(RESULT, { corrections: [] });
  assert.deepEqual(rows.map((r) => r.path), ["invoice_no", "total", "note", "line_items[0].amount"]);
});

test("a row carries display value, raw value, found, and the first located box", () => {
  const [invoice, total, note] = toFieldRows(RESULT, { corrections: [] }) as [FieldRow, FieldRow, FieldRow];
  assert.equal(invoice.value, "A-1");
  assert.equal(total.value, "12.5");
  assert.equal(total.raw, 12.5);
  assert.equal(invoice.found, true);
  assert.deepEqual(invoice.location, { page: 0, bbox: [0.1, 0.2, 0.3, 0.4] });
  assert.equal(total.location, null);
  assert.equal(note.found, false);
  assert.equal(note.value, "");
  assert.equal(invoice.documentId, "blob1");
});

test("the newest check per field wins", () => {
  const rows = toFieldRows(RESULT, { corrections: [WRONG_OLD_TOTAL, CONFIRMED_NEW_TOTAL] });
  assert.equal(rows.find((r) => r.path === "total")!.check, "correct");
  const older = toFieldRows(RESULT, { corrections: [CONFIRMED_NEW_TOTAL, WRONG_OLD_TOTAL] });
  assert.equal(older.find((r) => r.path === "total")!.check, "correct");
});

test("a wrong check shows its corrected value; an absence correction shows none", () => {
  const wrong = toFieldRows(RESULT, { corrections: [WRONG_OLD_TOTAL] }).find((r) => r.path === "total")!;
  assert.equal(wrong.check, "wrong");
  assert.equal(wrong.correctedValue, "13");
  const absent = toFieldRows(RESULT, { corrections: [{ ...WRONG_OLD_TOTAL, corrected_value: null, corrected_absence: true }] }).find((r) => r.path === "total")!;
  assert.equal(absent.check, "wrong");
  assert.equal(absent.correctedValue, null);
});

test("garbage reads as no rows", () => {
  assert.deepEqual(toFieldRows(null, null), []);
  assert.equal(toFieldRows(RESULT, "x").length, 4);
});

test("rowsToCheck counts rows that have a check", () => {
  const rows = toFieldRows(RESULT, { corrections: [CONFIRMED_NEW_TOTAL] });
  assert.deepEqual(rowsToCheck(rows), { checked: 1, total: 4 });
});

test("checkBody confirms with the raw predicted value and no annotator", () => {
  const row = toFieldRows(RESULT, { corrections: [] })[1]!;
  const body = checkBody(row, "correct", {}, "proc1", "2026-03-01T00:00:00Z");
  assert.deepEqual(body, {
    report_id: "proc1",
    document_id: "blob1",
    field_path: "total",
    predicted_value: 12.5,
    confirmed: true,
    reason: "",
    timestamp: "2026-03-01T00:00:00Z",
  });
});

test("checkBody for a wrong value carries the corrected value and reason", () => {
  const row = toFieldRows(RESULT, { corrections: [] })[1]!;
  const body = checkBody(row, "wrong", { value: "13", reason: "typo" }, "proc1", "t") as Record<string, unknown>;
  assert.equal(body.confirmed, false);
  assert.equal(body.corrected_value, "13");
  assert.equal(body.reason, "typo");
  assert.equal("annotator" in body, false);
});

test("checkBody for an absence says so and carries no value", () => {
  const row = toFieldRows(RESULT, { corrections: [] })[1]!;
  const body = checkBody(row, "wrong", { absent: true, reason: "none" }, "p", "t") as Record<string, unknown>;
  assert.equal(body.corrected_absence, true);
  assert.equal("corrected_value" in body, false);
});
