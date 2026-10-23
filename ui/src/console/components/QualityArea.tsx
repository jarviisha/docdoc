/**
 * How well a tenant's extractions read, scored against the fields its
 * operators checked: a stat strip, the fields that went wrong and the
 * documents they went wrong on, and the history of re-runs.
 *
 * **It decides nothing.** Every number, mark, and which evaluation is shown
 * are `console/model/quality.ts`'s.
 */

import { useCallback, useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import type { Intent } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import { shortId } from "../model/format.ts";
import {
  historyRows,
  rerunWarning,
  shownEvaluation,
  stillRunning,
  toAnswers,
  toFieldTable,
  toStatStrip,
  type EvaluationDetail,
  type EvaluationSummary,
} from "../model/quality.ts";
import { toTenants } from "../model/submit.ts";
import { ConfirmDialog } from "./ConfirmDialog.tsx";
import { CopyableId } from "./CopyableId.tsx";
import { DataTable } from "./DataTable.tsx";
import { SelectField } from "./fields.tsx";
import { SectionHeading } from "./SectionHeading.tsx";
import { usePolling } from "./usePolling.ts";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  onOpenRun: (runId: string) => void;
  presetTenant: string | null;
}

/** A history mark's colour; the word is always beside it. */
const MARK_TONE: Record<string, "info" | "warning" | "neutral"> = {
  running: "info",
  "Could not be scored": "warning",
  "schema changed": "warning",
  "not comparable": "neutral",
};

const evaluationsOf = (body: unknown): EvaluationSummary[] =>
  (body as { evaluations?: EvaluationSummary[] } | null)?.evaluations ?? [];

export function QualityArea({ request, onOpenRun, presetTenant }: Props) {
  const [tenants, setTenants] = useState<string[] | null>(null);
  const [tenant, setTenant] = useState(presetTenant ?? "");
  const [answers, setAnswers] = useState<ReturnType<typeof toAnswers> | null>(null);
  const [schema, setSchema] = useState("");
  const [list, setList] = useState<EvaluationSummary[] | null>(null);
  const [listReads, setListReads] = useState(0);
  const [chosen, setChosen] = useState<string | null>(null);
  const [detail, setDetail] = useState<(EvaluationSummary & EvaluationDetail) | null>(null);
  const [openPath, setOpenPath] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    void request({ type: "list-tenants" }).then((answer) => {
      if (!current) return;
      setTenants(answer.ok ? toTenants(answer.body) : []);
      if (!answer.ok) setProblem(describe(answer.failure));
    });
    return () => {
      current = false;
    };
  }, [request]);

  useEffect(() => {
    if (tenant === "") return;
    let current = true;
    setAnswers(null);
    void request({ type: "tenant-answers", tenantId: tenant }).then((answer) => {
      if (!current) return;
      const found = answer.ok ? toAnswers(answer.body) : [];
      if (!answer.ok) setProblem(describe(answer.failure));
      setAnswers(found);
      setSchema(found[0]?.value ?? "");
    });
    return () => {
      current = false;
    };
  }, [request, tenant]);

  const readList = useCallback(async () => {
    if (tenant === "" || schema === "") return;
    const answer = await request({ type: "list-evaluations", tenantId: tenant, schema });
    if (answer.ok) setList(evaluationsOf(answer.body));
    else setProblem(describe(answer.failure));
  }, [request, tenant, schema]);

  useEffect(() => {
    setList(null);
    setChosen(null);
    void readList();
  }, [readList, listReads]);
  usePolling(readList, list !== null && stillRunning(list));

  const shownId = list === null ? null : shownEvaluation(list, chosen);
  useEffect(() => {
    setDetail(null);
    setOpenPath(null);
    if (shownId === null) return;
    let current = true;
    void request({ type: "evaluation", tenantId: tenant, evaluationId: shownId }).then((answer) => {
      if (!current) return;
      if (answer.ok) setDetail(answer.body as EvaluationSummary & EvaluationDetail);
      else setProblem(describe(answer.failure));
    });
    return () => {
      current = false;
    };
  }, [request, tenant, shownId]);

  const documents = answers?.find((a) => a.value === schema)?.documents ?? 0;

  const start = async () => {
    const answer = await request({ type: "start-evaluation", tenantId: tenant, schema });
    setConfirming(false);
    if (answer.ok) {
      setProblem(null);
      setListReads((n) => n + 1);
    } else {
      setProblem(describe(answer.failure));
    }
  };

  const fields = detail === null ? [] : toFieldTable(detail);
  const summary = list?.find((e) => e.evaluation_id === shownId);
  const missed = fields.find((f) => f.path === openPath);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-end gap-2">
        <SelectField
          label="Tenant"
          className="w-[220px]"
          placeholder="Choose a tenant"
          options={tenants ?? []}
          value={tenant}
          onChange={setTenant}
        />
        <SelectField
          label="Schema"
          className="w-[260px]"
          placeholder="No checked schema"
          options={answers ?? []}
          value={schema}
          onChange={setSchema}
        />
        <Button variant="outline" disabled={schema === ""} onClick={() => setListReads((n) => n + 1)}>
          Refresh
        </Button>
        <div className="flex-1" />
        <Button disabled={schema === "" || (list !== null && stillRunning(list))} onClick={() => setConfirming(true)}>
          Re-run on checked documents
        </Button>
      </div>

      <div role="status" aria-live="polite" className="empty:-mb-3">
        {tenants === null || (tenant !== "" && answers === null) ? <p className="text-sm">Loading…</p> : null}
        {problem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>Quality could not be read</AlertTitle>
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        )}
        {tenant === "" && tenants !== null ? (
          <Alert variant="info">
            <AlertTitle>Choose a tenant to see how well its extractions read.</AlertTitle>
          </Alert>
        ) : null}
        {answers !== null && answers.length === 0 ? (
          <Alert variant="info">
            <AlertTitle>
              This tenant has checked no fields yet. Open one of its extraction runs and mark fields Correct or Wrong.
            </AlertTitle>
          </Alert>
        ) : null}
        {list !== null && list.length === 0 ? (
          <Alert variant="info">
            <AlertTitle>This schema has not been scored yet. Re-run on checked documents to score it.</AlertTitle>
          </Alert>
        ) : null}
        {list !== null && stillRunning(list) ? (
          <Alert variant="info">
            <AlertTitle>A re-run is in progress; this page refreshes when it finishes.</AlertTitle>
          </Alert>
        ) : null}
      </div>

      <ConfirmDialog
        open={confirming}
        onOpenChange={setConfirming}
        title="Re-run on checked documents?"
        description={rerunWarning(documents)}
        confirmLabel="Re-run"
        cancelLabel="Keep the last report"
        onConfirm={start}
      />

      {detail === null || summary === undefined ? null : (
        <>
          <div className="flex divide-x rounded-sm border">
            {/* The listed summary: the detail body's `documents` is the drill-down list, not the count. */}
            {toStatStrip(summary).map((stat) => (
              <div key={stat.label} className="flex-1 px-3 py-2">
                <p className="text-[11px] text-slate-500">{stat.label}</p>
                <p className="text-base font-semibold tabular-nums">{stat.value}</p>
                <p className="text-[11px] text-slate-500 tabular-nums">{stat.detail}</p>
              </div>
            ))}
          </div>

          <SectionHeading>Fields</SectionHeading>
          <Card className="py-0">
            <DataTable
              rows={fields}
              rowKey={(field) => field.path}
              label="Fields, as checked"
              columns={[
                { key: "path", header: "Field", cell: (f) => <code className="font-mono text-xs">{f.path}</code> },
                { key: "correct", header: "Correct", width: "100px", align: "right", cell: (f) => f.correct },
                { key: "wrong", header: "Wrong", width: "100px", align: "right", cell: (f) => f.wrong },
                { key: "missing", header: "Missing", width: "100px", align: "right", cell: (f) => f.missing },
                {
                  key: "documents",
                  header: "Documents",
                  width: "200px",
                  cell: (f) =>
                    f.documents.length === 0 ? (
                      "—"
                    ) : (
                      <Button
                        variant="link"
                        className="h-auto p-0 text-xs"
                        aria-expanded={openPath === f.path}
                        onClick={() => setOpenPath(openPath === f.path ? null : f.path)}
                      >
                        See the {f.documents.length} document(s) →
                      </Button>
                    ),
                },
              ]}
            />
          </Card>

          {missed === undefined ? null : (
            <div className="flex flex-col gap-1">
              <SectionHeading>Documents that missed {missed.path}</SectionHeading>
              <ul className="flex flex-col gap-1">
                {missed.documents.map((d) => (
                  <li key={d.documentId}>
                    <CopyableId
                      value={d.documentId}
                      display={shortId(d.documentId)}
                      {...(d.runId === null ? {} : { onOpen: () => onOpenRun(d.runId!) })}
                    />
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}

      {list === null || list.length === 0 ? null : (
        <>
          <SectionHeading>History</SectionHeading>
          <Card className="py-0">
            <DataTable
              rows={historyRows(list)}
              rowKey={(row) => row.evaluationId}
              label="Re-runs, newest first"
              columns={[
                { key: "when", header: "When", width: "200px", cell: (row) => row.when },
                { key: "accuracy", header: "Fields correct", width: "140px", align: "right", cell: (row) => row.accuracy },
                {
                  key: "marks",
                  header: "Against the one before",
                  cell: (row) => (
                    <span className="flex gap-1">
                      {row.marks.map((mark) => (
                        <Badge key={mark} variant={MARK_TONE[mark] ?? "neutral"}>
                          {mark}
                        </Badge>
                      ))}
                    </span>
                  ),
                },
                {
                  key: "evaluation",
                  header: "Report",
                  width: "140px",
                  cell: (row) => (
                    <CopyableId
                      value={row.evaluationId}
                      display={shortId(row.evaluationId)}
                      onOpen={() => setChosen(row.evaluationId)}
                    />
                  ),
                },
              ]}
            />
          </Card>
        </>
      )}
    </div>
  );
}
