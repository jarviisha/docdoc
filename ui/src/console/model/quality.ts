/** A tenant's answers scored: the stat strip, per-field table, and run history. */

import { when } from "./format.ts";

interface Metric {
  numerator: number;
  denominator: number;
}
type Metrics = Record<string, Metric> | null;

export interface EvaluationSummary {
  evaluation_id: string;
  schema_identity: string;
  created_at: string;
  documents: number;
  erased_documents: number;
  metrics: Metrics;
  golden_set_id: string;
  /** "failed": it could not be scored (its schema version is gone or changed). Terminal, like "finished". */
  status?: "running" | "finished" | "failed";
  /** Why it failed: an exception class. */
  failure?: string | null;
}

export interface EvaluationDetail {
  per_field: Record<string, Record<string, Metric>> | null;
  documents: {
    document_id: string;
    run_id?: string;
    outcomes: { field_path: string; kind: string | null }[];
  }[];
}

const dash = { value: "—", detail: "" };

function percent(numerator: number, denominator: number): { value: string; detail: string } {
  if (denominator === 0) return dash;
  return {
    value: `${((numerator / denominator) * 100).toFixed(1)}%`,
    detail: `(${numerator}/${denominator})`,
  };
}

const share = (m: Metric | undefined) => (m ? percent(m.numerator, m.denominator) : dash);

export function toStatStrip(
  summary: Pick<EvaluationSummary, "documents" | "erased_documents" | "metrics">,
): { label: string; value: string; detail: string }[] {
  const metrics = summary.metrics ?? {};
  const mislocated = metrics.mislocation_rate;
  const strip = [
    {
      label: "Checked documents",
      value: String(summary.documents),
      detail: summary.erased_documents > 0 ? `${summary.erased_documents} erased` : "",
    },
    { label: "Fields correct", ...share(metrics.field_accuracy) },
    { label: "Located on page", ...share(metrics.grounding_rate) },
    { label: "Missing", ...share(metrics.missing_rate) },
  ];
  // Only where expected locations exist to measure against.
  return mislocated && mislocated.denominator > 0
    ? [...strip, { label: "Read from a different place", ...share(mislocated) }]
    : strip;
}

export function toFieldTable(
  detail: EvaluationDetail,
): {
  path: string;
  correct: number;
  wrong: number;
  missing: number;
  documents: { documentId: string; runId: string | null }[];
}[] {
  return Object.entries(detail.per_field ?? {}).map(([path, metrics]) => ({
    path,
    correct: metrics.field_accuracy?.numerator ?? 0,
    wrong: metrics.incorrect_rate?.numerator ?? 0,
    missing: metrics.missing_rate?.numerator ?? 0,
    documents: detail.documents
      .filter((d) => d.outcomes.some((o) => o.field_path === path && o.kind !== "correct"))
      .map((d) => ({ documentId: d.document_id, runId: d.run_id ?? null })),
  }));
}

/** Newest first, as the route lists them. */
export function historyRows(list: EvaluationSummary[]): {
  evaluationId: string;
  when: string;
  accuracy: string;
  schemaChanged: boolean;
  comparable: boolean;
  /** "running" or "Could not be scored", then tags against the run before: "schema changed", "not comparable". */
  marks: string[];
}[] {
  return list.map((e, i) => {
    const older = list[i + 1];
    const schemaChanged = older !== undefined && older.schema_identity !== e.schema_identity;
    const comparable = older !== undefined && older.golden_set_id === e.golden_set_id;
    return {
      evaluationId: e.evaluation_id,
      when: when(e.created_at),
      accuracy: share(e.metrics?.field_accuracy).value,
      schemaChanged,
      comparable,
      marks: [
        ...(e.status === "running" ? ["running"] : []),
        ...(e.status === "failed" ? ["Could not be scored"] : []),
        ...(schemaChanged ? ["schema changed"] : []),
        ...(older !== undefined && !comparable ? ["not comparable"] : []),
      ],
    };
  });
}

export function rerunWarning(documents: number): string {
  return `This re-runs ${documents} document(s) with the current configuration. Unchanged steps are reused; changed ones may call paid providers.`;
}

/** The evaluation the report shows: the one chosen if still listed, else the newest with a report. */
export function shownEvaluation(
  list: Pick<EvaluationSummary, "evaluation_id" | "status">[],
  chosen: string | null,
): string | null {
  if (chosen !== null && list.some((e) => e.evaluation_id === chosen)) return chosen;
  return list.find((e) => e.status !== "running" && e.status !== "failed")?.evaluation_id ?? null;
}

/** Whether the list is read again: its newest evaluation has not settled. */
export function stillRunning(list: Pick<EvaluationSummary, "status">[]): boolean {
  return list[0]?.status === "running";
}

/** `tenant-answers` as schema choices, each with the documents it would re-run. */
export function toAnswers(body: unknown): { value: string; label: string; documents: number }[] {
  const schemas = (body as { schemas?: unknown } | null)?.schemas;
  if (!Array.isArray(schemas)) return [];
  return schemas.map((s: { schema_name: string; documents: number }) => ({
    value: s.schema_name,
    label: `${s.schema_name} · ${s.documents} documents`,
    documents: s.documents,
  }));
}
