/**
 * A run's result becomes what should be shown. Nothing here renders.
 *
 * **Coordinates are passed through untouched, and that is a finding rather than
 * laziness.** The parsers resolve rotation *before* normalizing — `pdf_text.py`
 * maps every word box through `page.rotation_matrix` and normalizes against
 * `page.rect`, the displayed rect; the other two adapters record `rotation = 0`
 * because their services already report displayed coordinates. So a box that
 * reaches here is already oriented the way the page is shown, and the correct
 * action is none (research R1, FR-023, FR-024).
 *
 * Reading `Page.rotation` and rotating the overlay is the plausible wrong
 * implementation of this file. It double-rotates, and it fails *only* on rotated
 * pages — the population FR-024 exists to protect — so nothing catches it.
 */

import type {
  BoxEntry,
  GeometryState,
  RunView,
  ScoreView,
  ValueRow,
  WireOutcome,
  WireRun,
  WireValue,
} from "./types.ts";

const STATUS_LABEL: Record<string, string> = {
  exact: "Exact match",
  fuzzy: "Fuzzy match",
  ungrounded: "Not located",
};

const GEOMETRY_LABEL: Record<GeometryState["kind"], string> = {
  // Deliberately three different sentences. "Not found" for either of the first
  // two would destroy a distinction the engine spends code to preserve (FR-018).
  unavailable: "Located in the text; this parser supplied no page geometry",
  empty: "Located in the text; the range covers no page tokens",
  located: "Located on the page",
  // Unreachable through `labelsFor`, which answers the `not-applicable` case
  // from the situation instead. Present so the record stays total.
  "not-applicable": "No location",
};

/**
 * Which of three worlds a row is in — and the reason this exists at all.
 *
 * **Three defects in this milestone were one defect.** Each label was derived
 * from `outcome === undefined`, which does not mean what it was read to mean:
 * it says *grounding has nothing to say about this field*, and that is true in
 * two unrelated situations. The model reported the field absent, so there was
 * never anything to ground. Or grounding never ran at all, because the run
 * failed at that stage and the response carries `extract` alone. A sentence
 * written for the first is false in the second.
 *
 *   T080  an asserted value read "there is no value to locate", printed beside
 *         the value it denied. A person saw it; no test could.
 *   T096  a run failing at `ground` labelled every asserted value "Reported
 *         absent", contradicting the row's own `presence` field.
 *
 * Fixing each sentence in turn is what allowed the second. So the situation is
 * named **once**, and both labels are answered from it — a row cannot say
 * "asserted" and "absent" at the same time because one value decides both.
 * `test/labels.test.ts` holds the rule over every stage combination a run can
 * fail in.
 */
type Situation = "absent" | "not-grounded" | "grounded";

function situationOf(presence: "asserted" | "absent", outcome: WireOutcome | undefined): Situation {
  if (presence === "absent") return "absent";
  // Asserted, but no grounding outcome exists: grounding did not run. Not the
  // same as "the grounder looked and failed", which arrives as an outcome whose
  // status is `ungrounded`.
  return outcome === undefined ? "not-grounded" : "grounded";
}

function labelsFor(
  situation: Situation,
  outcome: WireOutcome | undefined,
  geometry: GeometryState,
): { status: string; geometry: string } {
  switch (situation) {
    case "absent":
      return {
        status: "Reported absent",
        geometry: "No location, because there is no value to locate",
      };

    case "not-grounded":
      // Neither stage concluded anything about this value, and saying either
      // "not located" or "absent" would attribute a conclusion to a stage that
      // never ran.
      return {
        status: "Grounding did not run for this value",
        geometry: "No location, because grounding did not run",
      };

    case "grounded":
      return {
        status: STATUS_LABEL[outcome?.status ?? ""] ?? "",
        geometry:
          geometry.kind === "not-applicable"
            ? "Not located, so there is no rectangle to draw"
            : GEOMETRY_LABEL[geometry.kind],
      };
  }
}

/** Walk the value tree and yield every leaf — the ones carrying `field_path`. */
function* leaves(node: unknown): Generator<WireValue> {
  if (node === null || typeof node !== "object") return;
  if (Array.isArray(node)) {
    for (const item of node) yield* leaves(item);
    return;
  }
  const record = node as Record<string, unknown>;
  if (typeof record["field_path"] === "string" && "present" in record) {
    yield record as unknown as WireValue;
    return;
  }
  for (const child of Object.values(record)) yield* leaves(child);
}

function toBoxes(outcome: WireOutcome): BoxEntry[] {
  // One entry per box the run returned. Not the first, not a merged hull —
  // a value wrapping across two lines has two, and drawing one is a wrong
  // answer that looks like a right one (FR-015, SC-001).
  return (outcome.geometry ?? []).map(([pageIndex, [x0, y0, x1, y1]]) => ({
    pageIndex,
    x0,
    y0,
    x1,
    y1,
    fieldPath: outcome.field_path,
  }));
}

function toGeometryState(outcome: WireOutcome | undefined): GeometryState {
  if (outcome === undefined || outcome.status === "ungrounded") {
    return { kind: "not-applicable" };
  }
  if (outcome.geometry === null) return { kind: "unavailable" };
  if (outcome.geometry.length === 0) return { kind: "empty" };
  return { kind: "located", boxes: toBoxes(outcome) };
}

function toScore(outcome: WireOutcome | undefined): ScoreView | null {
  if (outcome === undefined || outcome.score === null) return null;
  if (outcome.status === "ungrounded") return null;
  // The tier travels with the number, always. An exact score is 1.0 by
  // definition and a fuzzy score is a measured similarity; a bare number invites
  // the comparison ADR-0004 forbids (FR-020).
  return { value: outcome.score, tier: outcome.status };
}

/**
 * Severity order, so "worst" is computed rather than assumed.
 *
 * Only `error` moves the run's verdict (`src/docdoc/validation/severity.py`);
 * the other two are recorded and deliberately powerless. A row reports the worst
 * of its field's findings all the same, because a value that failed a constraint
 * is not made less wrong by also being ungrounded.
 */
const SEVERITY_RANK: Record<string, number> = { error: 3, warning: 2, info: 1 };

/**
 * The worst severity recorded against one field, or `ok` if there is none.
 *
 * **This took the first finding until T100, and the first is not the worst.**
 * `assemble` emits findings in `sort_key` order — walk position, then entry
 * indices, then `check_id` — which within a single field is alphabetical. So
 * `total#grounding` arrives before `total#pattern`, and the default
 * `GroundingPolicy.ungrounded` is `warning` while a failed constraint is
 * `error`: a value that is both ungrounded *and* invalid was listed as
 * `warning`, understating the engine's own answer on the one row where the two
 * disagree.
 *
 * Understating is the direction that matters. A viewer whose entire subject is
 * telling a reader what is really there had no visible symptom for this — the
 * row renders perfectly, with the wrong word in it — which is why it survived
 * five convergence passes and a person looking at the screen.
 *
 * An unranked severity sorts below the three but still beats having no finding,
 * so a vocabulary this file has not heard of degrades to "something is wrong
 * here" rather than to `ok`.
 */
function verdictFor(fieldPath: string, run: WireRun): string {
  let worst: string | null = null;

  for (const finding of run.validation?.findings ?? []) {
    if (finding.field_path !== fieldPath) continue;
    if (worst === null || (SEVERITY_RANK[finding.severity] ?? 0) > (SEVERITY_RANK[worst] ?? 0)) {
      worst = finding.severity;
    }
  }

  return worst ?? "ok";
}

export function toRunView(run: WireRun, pageCount = 1): RunView {
  const outcomes = run.grounding?.outcomes ?? {};
  const values: ValueRow[] = [];

  for (const leaf of leaves(run.extraction?.values ?? {})) {
    const outcome = outcomes[leaf.field_path];
    const geometry = toGeometryState(outcome);
    const boxes = geometry.kind === "located" ? geometry.boxes : [];
    // A field the model reported absent has **no outcome at all** and is not
    // "ungrounded". Listing it as absent rather than omitting it is what makes
    // the count in SC-003 mean something (FR-019).
    const presence = leaf.present ? "asserted" : "absent";
    // One decision, two labels. They cannot disagree with each other or with
    // `presence`, because the same value produces all three.
    const labels = labelsFor(situationOf(presence, outcome), outcome, geometry);
    // Computed once and used twice. `verdict` and `labels.verdict` are the same
    // fact — the field and its textual equivalent (FR-057) — and calling the
    // function twice was how they could have come to differ.
    const verdict = verdictFor(leaf.field_path, run);

    values.push({
      fieldPath: leaf.field_path,
      value: leaf.value === null || leaf.value === undefined ? null : String(leaf.value),
      presence,
      verdict,
      status: outcome?.status ?? null,
      score: toScore(outcome),
      geometry,
      pages: outcome?.pages ?? [],
      boxes,
      labels: {
        status: labels.status,
        verdict,
        geometry: labels.geometry,
      },
    });
  }

  const boxesByPage = new Map<number, BoxEntry[]>();
  for (const row of values) {
    for (const box of row.boxes) {
      const existing = boxesByPage.get(box.pageIndex);
      if (existing === undefined) boxesByPage.set(box.pageIndex, [box]);
      else existing.push(box);
    }
  }

  return {
    values,
    boxesByPage,
    pagesToRender: [...boxesByPage.keys()].sort((a, b) => a - b),
    pageCount,
    selection: null,
  };
}

/** Selecting a field. Both directions are the same operation (FR-021). */
export function select(view: RunView, fieldPath: string | null): RunView {
  return { ...view, selection: fieldPath };
}

/** Which field a drawn box belongs to (FR-021, the reverse direction). */
export function fieldForBox(box: BoxEntry): string {
  return box.fieldPath;
}

/** Every page a selected field touches — plural, because it can be (FR-022). */
export function pagesForSelection(view: RunView): number[] {
  if (view.selection === null) return [];
  const row = view.values.find((candidate) => candidate.fieldPath === view.selection);
  return row === undefined ? [] : [...row.pages];
}
