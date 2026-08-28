/**
 * No label may contradict the row it sits on.
 *
 * Three defects in this milestone were one defect: a label derived from the
 * *absence of a record* rather than from the fact it describes. `outcome ===
 * undefined` means "grounding has nothing to say about this field", which is
 * true when the model reported the field absent **and** when grounding never
 * ran — and the viewer printed a sentence that is right only in the first case.
 *
 *   T080  an asserted value read "No location, because there is no value to
 *         locate", printed beside the value it denied. Found by a person
 *         looking at a screen, because nothing compared the two labels.
 *   T096  a run failing at the ground stage labelled every asserted value
 *         "Reported absent" — on rows whose own `presence` field said
 *         `asserted`. Found by running the code, because the tests for the
 *         partial-result path always supplied a grounding result too.
 *
 * Fixing each sentence in turn is what let the second happen. This file states
 * the rule instead, over every stage combination a run can actually fail in, so
 * the *class* fails the suite rather than waiting for the next reader.
 *
 * The rule: **a row's labels and its own fields must agree.** A row that says
 * `presence: "asserted"` and carries a value may not be labelled absent, and a
 * row that says `presence: "absent"` must be.
 */

import { strict as assert } from "node:assert";
import { readFileSync } from "node:fs";
import { describe, it } from "node:test";

import { toFailureView } from "../src/model/failure.ts";
import { toRunView } from "../src/model/run.ts";
import type { RunView, WireRun } from "../src/model/types.ts";

const RUN = JSON.parse(
  readFileSync(new URL("./fixtures/run.json", import.meta.url), "utf8"),
) as WireRun & { extraction: unknown; grounding: unknown; validation: unknown };

/**
 * Every shape a result can reach this model in.
 *
 * The partial ones are not hypothetical: a run failing at `ground` sends
 * `extract` alone, and a run failing at `validate` sends `extract` and `ground`.
 * Both come back through `toFailureView`, and until T096 the first mislabelled
 * every value it carried.
 */
const SHAPES: { name: string; view: () => RunView | null }[] = [
  { name: "a complete run", view: () => toRunView(RUN) },
  {
    name: "a run that failed at the ground stage (extract survived)",
    view: () =>
      toFailureView({
        error: { class: "GroundingError", stage: "ground", message: "grounding failed" },
        results: { extract: RUN.extraction },
      }).survivors,
  },
  {
    name: "a run that failed at the validate stage (extract and ground survived)",
    view: () =>
      toFailureView({
        error: { class: "ValidationError", stage: "validate", message: "a rule failed" },
        results: { extract: RUN.extraction, ground: RUN.grounding },
      }).survivors,
  },
];

const ABSENCE = /absent|no value to locate/i;

for (const shape of SHAPES) {
  describe(`labels agree with their row — ${shape.name}`, () => {
    it("never calls an asserted value absent", () => {
      const view = shape.view();
      assert.notEqual(view, null, "this shape must produce a view to check");

      const asserted = (view?.values ?? []).filter((row) => row.presence === "asserted");
      assert.ok(asserted.length > 0, "the fixture must contain asserted values");

      for (const row of asserted) {
        assert.doesNotMatch(
          row.labels.status,
          ABSENCE,
          `${row.fieldPath} is asserted (value ${row.value ?? "?"}) and its status label calls it absent`,
        );
        assert.doesNotMatch(
          row.labels.geometry,
          ABSENCE,
          `${row.fieldPath} is asserted (value ${row.value ?? "?"}) and its geometry label calls it absent`,
        );
      }
    });

    it("always says so when a field really is absent", () => {
      const view = shape.view();
      const absent = (view?.values ?? []).filter((row) => row.presence === "absent");
      assert.ok(absent.length > 0, "the fixture must contain absent fields");

      for (const row of absent) {
        assert.match(row.labels.status, ABSENCE, `${row.fieldPath} is absent and does not say so`);
        assert.equal(row.value, null, `${row.fieldPath} is absent yet carries a value`);
      }
    });

    it("gives every row a non-empty label for each distinction", () => {
      // FR-057: no distinction may rest on a colour the model never supplied.
      for (const row of shape.view()?.values ?? []) {
        assert.ok(row.labels.status.length > 0, `${row.fieldPath} has no status label`);
        assert.ok(row.labels.geometry.length > 0, `${row.fieldPath} has no geometry label`);
        assert.ok(row.labels.verdict.length > 0, `${row.fieldPath} has no verdict label`);
      }
    });

    it("distinguishes absent from asserted-and-unlocated by label", () => {
      // FR-019, the half that survives losing the overlay. Two rows that differ
      // in `presence` must not read alike.
      const view = shape.view();
      const absent = (view?.values ?? []).find((row) => row.presence === "absent");
      const unlocated = (view?.values ?? []).find(
        (row) => row.presence === "asserted" && row.boxes.length === 0,
      );

      if (absent === undefined || unlocated === undefined) return;
      assert.notEqual(absent.labels.status, unlocated.labels.status);
      assert.notEqual(absent.labels.geometry, unlocated.labels.geometry);
    });
  });
}

describe("a value whose grounding never ran", () => {
  it("is not described as though grounding had an answer", () => {
    // The specific sentence T096 produced. "Reported absent" and "Not located"
    // are both claims about what a stage concluded; neither stage concluded
    // anything here, because grounding did not run.
    const view = toFailureView({
      error: { class: "GroundingError", stage: "ground", message: "grounding failed" },
      results: { extract: RUN.extraction },
    }).survivors;

    const row = (view?.values ?? []).find((candidate) => candidate.presence === "asserted");
    assert.ok(row, "the extract-only survivor must carry asserted values");
    assert.match(
      row.labels.status,
      /did not run|not grounded/i,
      `expected a label saying grounding did not run, got "${row.labels.status}"`,
    );
  });
});
