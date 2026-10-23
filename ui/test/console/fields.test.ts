import assert from "node:assert/strict";
import { test } from "node:test";

import {
  canSave,
  checkBody,
  checkText,
  draftProblem,
  saveFailure,
  fieldPages,
  pagePosition,
  reportIdOf,
  rowsToCheck,
  saveLabel,
  shownValue,
  toSave,
  type Draft,
  type FieldRow,
  toFieldRows,
} from "../../src/console/model/fields.ts";

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

const correct: Draft = { verdict: "correct", value: "", absent: false, reason: "" };
const wrong = (over: Partial<Draft> = {}): Draft => ({ verdict: "wrong", value: "", absent: false, reason: "", ...over });

test("a wrong mark with neither a value nor an absence says what is missing", () => {
  assert.equal(draftProblem(wrong()), "Type the right value, or mark it not in the document.");
  assert.equal(draftProblem(wrong({ value: "  " })), "Type the right value, or mark it not in the document.");
  assert.equal(draftProblem(wrong({ value: "13", reason: "typo" })), null);
  assert.equal(draftProblem(wrong({ absent: true, reason: "not printed" })), null);
  assert.equal(draftProblem(correct), null);
});

test("a wrong mark with a blank reason asks for one; a correct one needs none", () => {
  assert.equal(draftProblem(wrong({ value: "13" })), "Add a reason.");
  assert.equal(draftProblem(wrong({ absent: true, reason: "  " })), "Add a reason.");
  assert.equal(draftProblem(correct), null);
  const rows = toFieldRows(RESULT, { corrections: [] });
  assert.equal(canSave(rows, { note: wrong({ value: "x" }) }), false);
});

test("a failed save names the field of every check that failed", () => {
  assert.equal(saveFailure([]), null);
  assert.equal(
    saveFailure([
      { path: "total", problem: "The value is not valid here." },
      { path: "line_items[0].quantity", problem: "Try again." },
    ]),
    "total: The value is not valid here. line_items[0].quantity: Try again.",
  );
});

test("only marks that differ from the saved check are saved", () => {
  const rows = toFieldRows(RESULT, { corrections: [CONFIRMED_NEW_TOTAL] });
  const drafts = { total: correct, invoice_no: correct, note: wrong({ absent: true }) };
  assert.deepEqual(toSave(rows, drafts).map((r) => r.path), ["invoice_no", "note"]);
  const was = toFieldRows(RESULT, { corrections: [WRONG_OLD_TOTAL] });
  assert.deepEqual(toSave(was, { total: wrong({ value: "13" }) }), []);
  assert.deepEqual(toSave(was, { total: wrong({ value: "14" }) }).map((r) => r.path), ["total"]);
  assert.deepEqual(toSave(was, { total: wrong({ absent: true }) }).map((r) => r.path), ["total"]);
});

test("save is armed by at least one change and no incomplete wrong mark", () => {
  const rows = toFieldRows(RESULT, { corrections: [] });
  assert.equal(canSave(rows, {}), false);
  assert.equal(canSave(rows, { total: correct }), true);
  assert.equal(canSave(rows, { total: correct, note: wrong() }), false);
  assert.equal(canSave(rows, { total: correct, note: wrong({ value: "x", reason: "r" }) }), true);
});

test("the save label counts fields checked once saved", () => {
  const rows = toFieldRows(RESULT, { corrections: [WRONG_OLD_TOTAL] });
  assert.equal(saveLabel(rows, {}), "Save check (1 of 4 fields)");
  assert.equal(saveLabel(rows, { total: correct, note: correct }), "Save check (2 of 4 fields)");
});

test("a row's extracted value and saved check read as text", () => {
  const rows = toFieldRows(RESULT, { corrections: [WRONG_OLD_TOTAL] });
  assert.equal(shownValue(rows[0]!), "A-1");
  assert.equal(shownValue(rows[2]!), "— not found");
  assert.equal(checkText(rows[1]!), "Wrong, should be 13");
  assert.equal(checkText(rows[0]!), "");
  const absent = toFieldRows(RESULT, { corrections: [{ ...WRONG_OLD_TOTAL, corrected_absence: true }] });
  assert.equal(checkText(absent[1]!), "Wrong, not in the document");
  assert.equal(checkText(toFieldRows(RESULT, { corrections: [CONFIRMED_NEW_TOTAL] })[1]!), "Correct");
});

test("a check's report id is the run's processing id", () => {
  assert.equal(reportIdOf({ job_id: "sha256:abc" }), "sha256:abc");
  assert.equal(reportIdOf({}), "");
});

test("the page view shows every page up to the last located field", () => {
  const rows = toFieldRows(RESULT, { corrections: [] });
  assert.deepEqual(fieldPages(rows).map((p) => p.index), [0]);
  const later = rows.map((r) => (r.location ? { ...r, location: { ...r.location, page: 2 } } : r));
  const pages = fieldPages(later);
  assert.deepEqual(pages.map((p) => p.index), [0, 1, 2]);
  assert.deepEqual(pages[0]!.lines, []);
  assert.equal(pagePosition(pages, 2), 2);
  assert.equal(pagePosition(pages, 7), null);
  assert.deepEqual(fieldPages([]).map((p) => p.index), [0]);
});
