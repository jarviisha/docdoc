/**
 * An extraction run's fields as rows to check, and the body a check posts.
 *
 * Rows come from the run's own result: the values in the extraction's order,
 * each with where grounding found it. The newest check per field decides its
 * mark; an older one is history the route still keeps.
 */

import type { ResultPage } from "./result.ts";

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

/** An unsaved mark on one row: what the operator clicked and typed. */
export interface Draft {
  verdict: "correct" | "wrong";
  value: string;
  absent: boolean;
  reason: string;
}

/** Why a mark cannot be saved yet, or `null`. The route refuses a "wrong" that names no answer. */
export function draftProblem(draft: Draft): string | null {
  return draft.verdict === "wrong" && !draft.absent && draft.value.trim() === ""
    ? "Type the right value, or mark it not in the document."
    : null;
}

/** Whether a mark says something the newest saved check does not. */
function changes(row: FieldRow, draft: Draft): boolean {
  if (draft.verdict !== row.check) return true;
  if (draft.verdict === "correct") return false;
  return draft.absent ? row.correctedValue !== null : draft.value !== row.correctedValue;
}

/** The rows a save posts a check for, in table order. */
export function toSave(rows: FieldRow[], drafts: Record<string, Draft>): FieldRow[] {
  return rows.filter((r) => drafts[r.path] !== undefined && changes(r, drafts[r.path]!));
}

export function canSave(rows: FieldRow[], drafts: Record<string, Draft>): boolean {
  const saving = toSave(rows, drafts);
  return saving.length > 0 && saving.every((r) => draftProblem(drafts[r.path]!) === null);
}

/** "Save check (N of M fields)": N counts the fields checked once this save lands. */
export function saveLabel(rows: FieldRow[], drafts: Record<string, Draft>): string {
  const checked = rows.filter((r) => r.check !== null || drafts[r.path] !== undefined).length;
  return `Save check (${checked} of ${rows.length} fields)`;
}

export function shownValue(row: FieldRow): string {
  return row.found ? row.value : "— not found";
}

/** The newest saved check, as the row says it. */
export function checkText(row: FieldRow): string {
  if (row.check === null) return "";
  if (row.check === "correct") return "Correct";
  return row.correctedValue === null ? "Wrong, not in the document" : `Wrong, should be ${row.correctedValue}`;
}

/** A check names the result it was made against: the run's processing id. */
export function reportIdOf(result: unknown): string {
  const id = isObj(result) ? result.job_id : undefined;
  return typeof id === "string" ? id : "";
}

/**
 * Pages for the page view: 0 through the last page a field was located on.
 * The result carries no page count or size; the image's own size frames it.
 */
// ponytail: pages past the last located field are not offered; a page count in the result would fix it.
export function fieldPages(rows: FieldRow[]): ResultPage[] {
  const last = Math.max(0, ...rows.map((r) => r.location?.page ?? 0));
  return Array.from({ length: last + 1 }, (_, index) => ({ index, width: 1, height: 1, lines: [], angle: null }));
}

/** Where page `index` sits among `pages`, or `null` if it is not there. */
export function pagePosition(pages: ResultPage[], index: number): number | null {
  const at = pages.findIndex((p) => p.index === index);
  return at === -1 ? null : at;
}
