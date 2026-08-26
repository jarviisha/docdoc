/**
 * The view model, against a real run.
 *
 * `fixtures/run.json` is an actual `POST /v1/extract` response over the
 * committed fixture PDF, not a hand-written shape. That matters more than usual
 * here: every claim in this file is about faithfulness to what the engine
 * returned, and a fixture written by hand would be a claim about what its author
 * believed the engine returns.
 *
 * On that document the pipeline locates 5 of 13 asserted values, one of them
 * across two boxes, and reports 2 fields absent. Those numbers are the subject
 * of several tests below and are properties of the fixture, so they are read
 * from it rather than typed in — except where typing them in *is* the assertion.
 */

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import { fieldForBox, pagesForSelection, select, toRunView } from "../src/model/run.ts";
import type { WireOutcome, WireRun } from "../src/model/types.ts";

const RUN = JSON.parse(
  readFileSync(new URL("./fixtures/run.json", import.meta.url), "utf8"),
) as WireRun;

const outcomes = () => RUN.grounding?.outcomes ?? {};

describe("boxes", () => {
  it("emits exactly one entry per box the run returned", () => {
    // SC-001, invariant 1. Not "at least one" — equal. The fixture contains a
    // value carrying two boxes, so "the first box" would pass a weaker test.
    const view = toRunView(RUN);

    for (const [path, outcome] of Object.entries(outcomes())) {
      const row = view.values.find((candidate) => candidate.fieldPath === path);
      if (row === undefined) continue;
      assert.equal(row.boxes.length, (outcome.geometry ?? []).length, `box count for ${path}`);
    }
  });

  it("does not collapse a multi-box value to one box", () => {
    const multi = Object.values(outcomes()).find(
      (outcome) => (outcome.geometry ?? []).length > 1,
    ) as WireOutcome;
    assert.ok(multi, "the fixture should contain a value spanning more than one box");

    const view = toRunView(RUN);
    const row = view.values.find((candidate) => candidate.fieldPath === multi.field_path);

    assert.equal(row?.boxes.length, multi.geometry?.length);
    assert.ok((row?.boxes.length ?? 0) > 1);
  });

  it("passes coordinates through unchanged", () => {
    // FR-023, invariant 2, research R1. The parsers resolved rotation before
    // normalizing, so the correct transformation here is none. Any rounding,
    // clamping, flipping or hull-merging would show up as a difference.
    const view = toRunView(RUN);

    for (const [path, outcome] of Object.entries(outcomes())) {
      const row = view.values.find((candidate) => candidate.fieldPath === path);
      (outcome.geometry ?? []).forEach(([pageIndex, [x0, y0, x1, y1]], index) => {
        const box = row?.boxes[index];
        assert.deepEqual(
          [box?.pageIndex, box?.x0, box?.y0, box?.x1, box?.y1],
          [pageIndex, x0, y0, x1, y1],
          `coordinates for ${path}[${index}]`,
        );
      });
    }
  });

  it("emits no box for an ungrounded value, and still lists it", () => {
    // SC-002, both halves together: a model that emits nothing satisfies the
    // first alone, and one that emits everywhere satisfies the second alone.
    const view = toRunView(RUN);
    const ungrounded = Object.values(outcomes()).filter(
      (outcome) => outcome.status === "ungrounded",
    );
    assert.ok(ungrounded.length > 0, "the fixture should contain ungrounded values");

    for (const outcome of ungrounded) {
      const row = view.values.find((candidate) => candidate.fieldPath === outcome.field_path);
      assert.ok(row, `${outcome.field_path} must still be listed`);
      assert.equal(row?.boxes.length, 0, `${outcome.field_path} must have no box`);
    }
  });

  it("groups boxes by page without losing any", () => {
    const view = toRunView(RUN);
    const grouped = [...view.boxesByPage.values()].flat().length;
    const flat = view.values.reduce((total, row) => total + row.boxes.length, 0);

    assert.equal(grouped, flat);
  });
});

describe("the list", () => {
  it("lists every value the run produced, absent ones included", () => {
    // SC-003, both halves. Counting only asserted values would let a viewer drop
    // every absent field silently.
    const view = toRunView(RUN);
    const wire = JSON.stringify(RUN.extraction?.values ?? {});
    const leafCount = (wire.match(/"field_path"/g) ?? []).length;

    assert.equal(view.values.length, leafCount);
    assert.ok(
      view.values.some((row) => row.presence === "absent"),
      "the fixture should contain a field the model reported absent",
    );
  });

  it("distinguishes a field reported absent from one that could not be grounded", () => {
    // FR-019. An absent field has no grounding outcome *at all* — it is not
    // ungrounded, and a viewer that renders them alike has invented a fact.
    const view = toRunView(RUN);
    const absent = view.values.filter((row) => row.presence === "absent");
    const ungrounded = view.values.filter((row) => row.status === "ungrounded");

    assert.ok(absent.length > 0 && ungrounded.length > 0);
    for (const row of absent) {
      assert.equal(row.status, null);
      assert.equal(row.geometry.kind, "not-applicable");
      assert.notEqual(row.labels.status, ungrounded[0]?.labels.status);
    }
  });

  it("does not tell a reader an asserted value has no value", () => {
    // T080, found by looking at the screen. `currency = USD` and `total =
    // 1240.00` are asserted values the grounder could not place, and both were
    // labelled "No location, because there is no value to locate" — a sentence
    // that is true of an absent field and false of these, printed next to the
    // value it denied. Every test passed: the status badges already read "Not
    // located" and "Reported absent", so no *fact* was lost and nothing
    // compared the two geometry labels.
    const view = toRunView(RUN);
    const asserted = view.values.filter(
      (row) => row.presence === "asserted" && row.geometry.kind === "not-applicable",
    );
    const absent = view.values.filter((row) => row.presence === "absent");

    assert.ok(asserted.length > 0 && absent.length > 0);

    for (const row of asserted) {
      assert.doesNotMatch(
        row.labels.geometry,
        /no value/i,
        `${row.fieldPath} has the value ${row.value ?? "?"} and its label denies it`,
      );
    }

    // And the two still read differently, which is the half FR-019 asks for.
    assert.notEqual(asserted[0]?.labels.geometry, absent[0]?.labels.geometry);
  });

  it("carries every fact the overlay conveys", () => {
    // FR-055, SC-017 — losing the overlay must lose the picture and no fact.
    const view = toRunView(RUN);

    for (const row of view.values) {
      assert.equal(typeof row.fieldPath, "string");
      assert.equal(typeof row.verdict, "string");
      assert.ok(Array.isArray(row.pages));
      assert.equal(typeof row.labels.status, "string");
      assert.equal(typeof row.labels.geometry, "string");
      assert.ok(row.labels.geometry.length > 0);
    }
  });

  it("never emits a bare score", () => {
    // FR-020, invariant 6. An exact score is 1.0 by definition and a fuzzy score
    // is measured; the tier is what stops them being compared.
    const view = toRunView(RUN);

    for (const row of view.values) {
      if (row.score === null) continue;
      assert.ok(row.score.tier === "exact" || row.score.tier === "fuzzy");
      assert.equal(typeof row.score.value, "number");
    }
  });

  it("gives an ungrounded value no score at all", () => {
    const view = toRunView(RUN);

    for (const row of view.values.filter((candidate) => candidate.status === "ungrounded")) {
      assert.equal(row.score, null);
    }
  });
});

describe("the three geometry states", () => {
  // SC-004. The real fixture contains only `located` and `not-applicable`, so
  // the other two are constructed — which is what the criterion asks for: "a
  // fixture set containing all three geometry states".
  const withGeometry = (geometry: WireOutcome["geometry"]): WireRun => ({
    document_id: null,
    schema_identity: "test@1",
    verdict: "valid",
    extraction: {
      values: {
        f: { field_path: "f", value: "x", present: true, claimed_text: "x" },
      },
    },
    grounding: {
      outcomes: {
        f: { field_path: "f", status: "exact", score: 1, span: [0, 1], pages: [0], geometry },
      },
    },
    validation: { verdict: "valid", findings: [] },
  });

  it("keeps unavailable, empty and located apart", () => {
    const unavailable = toRunView(withGeometry(null)).values[0];
    const empty = toRunView(withGeometry([])).values[0];
    const located = toRunView(withGeometry([[0, [0, 0, 1, 1]]])).values[0];

    assert.equal(unavailable?.geometry.kind, "unavailable");
    assert.equal(empty?.geometry.kind, "empty");
    assert.equal(located?.geometry.kind, "located");

    const labels = [unavailable, empty, located].map((row) => row?.labels.geometry);
    assert.equal(new Set(labels).size, 3, "three states, three labels");
  });

  it("labels neither unavailable nor empty as 'not found'", () => {
    for (const geometry of [null, [] as WireOutcome["geometry"]]) {
      const row = toRunView(withGeometry(geometry)).values[0];
      assert.doesNotMatch(row?.labels.geometry ?? "", /not found/i);
      assert.equal(row?.boxes.length, 0);
    }
  });
});

describe("selection", () => {
  it("resolves a box back to its field", () => {
    const view = toRunView(RUN);
    const box = [...view.boxesByPage.values()].flat()[0];

    assert.ok(box);
    assert.ok(view.values.some((row) => row.fieldPath === fieldForBox(box)));
  });

  it("exposes every page a selected field touches, not one", () => {
    // FR-022 — a value spanning two pages cannot have "the" page.
    const view = toRunView(RUN);
    const located = view.values.find((row) => row.boxes.length > 0);
    assert.ok(located);

    const pages = pagesForSelection(select(view, located.fieldPath));

    assert.deepEqual(pages, located.pages);
  });

  it("selects and deselects without touching anything else", () => {
    const view = toRunView(RUN);
    const selected = select(view, "invoice_number");

    assert.equal(selected.selection, "invoice_number");
    assert.equal(select(selected, null).selection, null);
    assert.deepEqual(selected.values, view.values);
  });
});
