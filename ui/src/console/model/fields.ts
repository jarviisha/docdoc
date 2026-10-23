/**
 * An extraction run's fields as rows to check, and the body a check posts.
 *
 * Rows come from the run's own result: the values in the extraction's order,
 * each with where grounding found it. The newest check per field decides its
 * mark; an older one is history the route still keeps.
 */

export interface FieldRow {
  path: string;
  /** What the table shows; empty when the field was not found. */
  value: string;
  /** The value as the run recorded it, and what a check's `predicted_value` carries. */
  raw: unknown;
  found: boolean;
  location: { page: number; bbox: [number, number, number, number] } | null;
  check: "correct" | "wrong" | null;
  correctedValue: string | null;
  documentId: string;
}

type Obj = Record<string, unknown>;
const isObj = (x: unknown): x is Obj => typeof x === "object" && x !== null && !Array.isArray(x);

const show = (v: unknown): string =>
  v === null || v === undefined ? "" : typeof v === "string" ? v : JSON.stringify(v);

/** Leaves of `values` in order: an object with a string `field_path` and a `present` flag. */
function leaves(node: unknown): Obj[] {
  if (Array.isArray(node)) return node.flatMap(leaves);
  if (!isObj(node)) return [];
  if (typeof node.field_path === "string" && "present" in node) return [node];
  return Object.values(node).flatMap(leaves);
}

export function toFieldRows(result: unknown, checks: unknown): FieldRow[] {
  const root = isObj(result) ? result : {};
  const extraction = isObj(root.extraction) ? root.extraction : {};
  const provenance = isObj(extraction.provenance) ? extraction.provenance : {};
  const grounding = isObj(root.grounding) && isObj(root.grounding.outcomes) ? root.grounding.outcomes : {};
  const list = isObj(checks) && Array.isArray(checks.corrections) ? (checks.corrections as Obj[]) : [];

  // Latest per path; a tie goes to the one listed later.
  const latest = new Map<string, Obj>();
  for (const c of list) {
    const seen = latest.get(String(c.field_path));
    if (seen === undefined || Date.parse(String(c.timestamp)) >= Date.parse(String(seen.timestamp))) {
      latest.set(String(c.field_path), c);
    }
  }

  return leaves(extraction.values).map((leaf) => {
    const path = leaf.field_path as string;
    const outcome = grounding[path];
    const geometry = isObj(outcome) && Array.isArray(outcome.geometry) ? outcome.geometry[0] : null;
    const check = latest.get(path);
    return {
      path,
      value: show(leaf.value),
      raw: leaf.value ?? null,
      found: leaf.present === true,
      location: Array.isArray(geometry)
        ? { page: geometry[0] as number, bbox: geometry[1] as [number, number, number, number] }
        : null,
      check: check === undefined ? null : check.confirmed ? "correct" : "wrong",
      correctedValue:
        check === undefined || check.confirmed || check.corrected_absence ? null : show(check.corrected_value),
      documentId: String(provenance.document_id ?? ""),
    };
  });
}

/**
 * The `Correction` JSON for one check. `annotator` is left out: the console
 * route records the signed-in operator. A tick carries no location; the route
 * takes it from the run's grounding.
 */
export function checkBody(
  row: FieldRow,
  verdict: "correct" | "wrong",
  input: { value?: string; absent?: boolean; reason?: string },
  reportId: string,
  at: string = new Date().toISOString(),
): object {
  const body = {
    report_id: reportId,
    document_id: row.documentId,
    field_path: row.path,
    predicted_value: row.raw,
    reason: input.reason ?? "",
    timestamp: at,
  };
  if (verdict === "correct") return { ...body, confirmed: true };
  return input.absent
    ? { ...body, confirmed: false, corrected_absence: true }
    : { ...body, confirmed: false, corrected_value: input.value };
}

export function rowsToCheck(rows: FieldRow[]): { checked: number; total: number } {
  return { checked: rows.filter((r) => r.check !== null).length, total: rows.length };
}
