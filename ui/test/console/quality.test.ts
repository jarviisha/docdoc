import assert from "node:assert/strict";
import { test } from "node:test";

import { historyRows, rerunWarning, toFieldTable, toStatStrip } from "../../src/console/model/quality.ts";

const e = { documents: 1, erased_documents: 0 };
const m = (name: string, numerator: number, denominator: number) => ({
  name,
  value: denominator === 0 ? null : numerator / denominator,
  numerator,
  denominator,
});

test("the stat strip reads the micro metrics", () => {
  const strip = toStatStrip({
    documents: 12,
    erased_documents: 0,
    metrics: {
      field_accuracy: m("field_accuracy", 304, 333),
      grounding_rate: m("grounding_rate", 90, 100),
      mislocation_rate: m("mislocation_rate", 10, 100),
      missing_rate: m("missing_rate", 5, 200),
    },
  });
  assert.deepEqual(strip, [
    { label: "Checked documents", value: "12", detail: "" },
    { label: "Fields correct", value: "91.3%", detail: "(304/333)" },
    { label: "Located on page", value: "90.0%", detail: "(90/100)" },
    { label: "Missing", value: "2.5%", detail: "(5/200)" },
    { label: "Read from a different place", value: "10.0%", detail: "(10/100)" },
  ]);
});

test("mislocation is omitted when nothing could be measured", () => {
  const strip = toStatStrip({
    documents: 1,
    erased_documents: 0,
    metrics: { grounding_rate: m("grounding_rate", 1, 2), mislocation_rate: m("mislocation_rate", 0, 0) },
  });
  assert.deepEqual(strip.map((s) => s.label), ["Checked documents", "Fields correct", "Located on page", "Missing"]);
  assert.equal(strip[2]!.value, "50.0%");
});

test("a stat with no denominator reads as a dash", () => {
  const strip = toStatStrip({ documents: 1, erased_documents: 2, metrics: null });
  assert.deepEqual(strip[0], { label: "Checked documents", value: "1", detail: "2 erased" });
  assert.deepEqual(strip[1], { label: "Fields correct", value: "—", detail: "" });
});

test("the field table counts per path and names the documents that missed", () => {
  const table = toFieldTable({
    per_field: {
      total: { field_accuracy: m("field_accuracy", 1, 3), incorrect_rate: m("incorrect_rate", 1, 3), missing_rate: m("missing_rate", 1, 3) },
    },
    documents: [
      { document_id: "d1", run_id: "r1", outcomes: [{ field_path: "total", kind: "correct" }] },
      { document_id: "d2", run_id: "r2", outcomes: [{ field_path: "total", kind: "incorrect" }] },
      { document_id: "d3", run_id: "r3", outcomes: [{ field_path: "total", kind: "missing" }] },
    ],
  });
  assert.deepEqual(table, [{ path: "total", correct: 1, wrong: 1, missing: 1, documents: ["d2", "d3"] }]);
});

test("a running evaluation has an empty field table", () => {
  assert.deepEqual(toFieldTable({ per_field: null, documents: [] }), []);
});

test("a schema change is marked in the history", () => {
  const rows = historyRows([
    { ...e, evaluation_id: "e2", created_at: "2026-02-01T00:00:00Z", schema_identity: "invoice@2", golden_set_id: "b", metrics: null },
    { ...e, evaluation_id: "e1", created_at: "2026-01-01T00:00:00Z", schema_identity: "invoice@1", golden_set_id: "a", metrics: null },
  ]);
  assert.deepEqual(rows.map((r) => r.schemaChanged), [true, false]);
  assert.deepEqual(rows.map((r) => r.comparable), [false, false]);
});

test("runs over the same answers are comparable, and accuracy is formatted", () => {
  const rows = historyRows([
    { ...e, evaluation_id: "e2", created_at: "2026-02-01T00:00:00Z", schema_identity: "s", golden_set_id: "a", metrics: { field_accuracy: m("field_accuracy", 1, 2) } },
    { ...e, evaluation_id: "e1", created_at: "2026-01-01T00:00:00Z", schema_identity: "s", golden_set_id: "a", metrics: null },
  ]);
  assert.equal(rows[0]!.comparable, true);
  assert.equal(rows[0]!.schemaChanged, false);
  assert.equal(rows[0]!.accuracy, "50.0%");
  assert.equal(rows[1]!.accuracy, "—");
});

test("the re-run warning names the document count", () => {
  assert.equal(
    rerunWarning(3),
    "This re-runs 3 document(s) with the current configuration. Unchanged steps are reused; changed ones may call paid providers.",
  );
});
