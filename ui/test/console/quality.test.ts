import assert from "node:assert/strict";
import { test } from "node:test";

import { configurationOf, historyRows, rerunWarning, shownEvaluation, stillRunning, toAnswers, toFieldTable, toStatStrip } from "../../src/console/model/quality.ts";

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
  assert.deepEqual(table, [{ path: "total", correct: 1, wrong: 1, missing: 1, documents: [{ documentId: "d2", runId: "r2" }, { documentId: "d3", runId: "r3" }] }]);
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

test("history marks say what changed against the run before", () => {
  const rows = historyRows([
    { ...e, evaluation_id: "e3", created_at: "2026-03-01T00:00:00Z", schema_identity: "s@2", golden_set_id: "b", metrics: null, status: "running" },
    { ...e, evaluation_id: "e2", created_at: "2026-02-01T00:00:00Z", schema_identity: "s@1", golden_set_id: "a", metrics: null },
    { ...e, evaluation_id: "e1", created_at: "2026-01-01T00:00:00Z", schema_identity: "s@1", golden_set_id: "a", metrics: null },
  ]);
  assert.deepEqual(rows.map((r) => r.marks), [["running", "schema changed", "not comparable"], [], []]);
});

test("the newest finished evaluation is shown unless one is chosen", () => {
  const list = [
    { evaluation_id: "e3", status: "running" },
    { evaluation_id: "e2", status: "finished" },
    { evaluation_id: "e1", status: "finished" },
  ] as const;
  assert.equal(shownEvaluation([...list], null), "e2");
  assert.equal(shownEvaluation([...list], "e1"), "e1");
  assert.equal(shownEvaluation([...list], "gone"), "e2");
  assert.equal(shownEvaluation([{ evaluation_id: "e1", status: "running" as const }], null), null);
});

test("the list polls while its newest evaluation runs", () => {
  assert.equal(stillRunning([{ status: "running" as const }, { status: "finished" as const }]), true);
  assert.equal(stillRunning([{ status: "finished" as const }, { status: "running" as const }]), false);
  assert.equal(stillRunning([]), false);
});

test("answers read as schema choices with their document counts", () => {
  assert.deepEqual(toAnswers({ schemas: [{ schema_name: "invoice", documents: 3, fields: 12 }] }), [
    { value: "invoice", label: "invoice · 3 documents", documents: 3 },
  ]);
  assert.deepEqual(toAnswers(null), []);
});

test("a failed evaluation is terminal: not polled, not shown as the report, marked in the history", () => {
  const list = [
    { ...e, evaluation_id: "e2", created_at: "2026-02-01T00:00:00Z", schema_identity: "s@1", golden_set_id: "a", metrics: null, status: "failed" as const, failure: "SchemaError" },
    { ...e, evaluation_id: "e1", created_at: "2026-01-01T00:00:00Z", schema_identity: "s@1", golden_set_id: "a", metrics: null, status: "finished" as const },
  ];
  assert.equal(stillRunning(list), false);
  assert.equal(shownEvaluation(list, null), "e1");
  assert.deepEqual(historyRows(list)[0]!.marks, ["Could not be scored"]);
});

test("a history row names the model and parser that produced it", () => {
  const provenance = {
    model_ids: ["echo"],
    model_versions: ["1"],
    parser_ids: ["pdfplumber", "tesseract"],
    parser_versions: ["0.11", "5.3"],
    schema_hashes: ["sha256:aa"],
    prompt_hashes: ["sha256:bb"],
  };
  const rows = historyRows([
    { ...e, evaluation_id: "e2", created_at: "2026-02-01T00:00:00Z", schema_identity: "s", golden_set_id: "a", metrics: null, provenance },
    { ...e, evaluation_id: "e1", created_at: "2026-01-01T00:00:00Z", schema_identity: "s", golden_set_id: "a", metrics: null, provenance: null },
  ]);
  assert.equal(rows[0]!.configuration, "echo 1 · pdfplumber 0.11, tesseract 5.3");
  assert.equal(rows[1]!.configuration, "—");
  assert.equal(configurationOf(provenance), "echo 1 · pdfplumber 0.11, tesseract 5.3");
});
