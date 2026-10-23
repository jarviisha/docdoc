/**
 * How well a tenant's extractions read, scored against the fields its
 * operators checked: a stat strip, the fields that went wrong and the
 * documents they went wrong on, and the history of re-runs.
 *
 * **It decides nothing.** Every number, mark, and which evaluation is shown
 * are `console/model/quality.ts`'s.
 */

import { useState } from "react";
import { getRouteApi } from "@tanstack/react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import { describe } from "../model/failure.ts";
import { shortId } from "../model/format.ts";
import { POLL_MS } from "../model/polling.ts";
import {
  historyRows,
  rerunWarning,
  shownEvaluation,
  shownSchema,
  stillRunning,
  toAnswers,
  toFieldTable,
  toStatStrip,
  type EvaluationDetail,
  type EvaluationSummary,
} from "../model/quality.ts";
import { qualitySearch } from "../model/search.ts";
import { toTenants } from "../model/submit.ts";
import { ConfirmDialog } from "./ConfirmDialog.tsx";
import { CopyableId } from "./CopyableId.tsx";
import { DataTable } from "./DataTable.tsx";
import { SelectField } from "./fields.tsx";
import { SectionHeading } from "./SectionHeading.tsx";
import { useAct, useIntent } from "./useIntent.ts";

const qualityApi = getRouteApi("/quality");

interface Props {
  onOpenRun: (runId: string) => void;
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

/**
 * The tenant, schema and evaluation in the URL are the ones shown; each pick
 * pushes, so Back walks back through them. A schema the URL does not name is
 * the first answer's, and stays out of the URL until the operator picks one.
 */
export function QualityArea({ onOpenRun }: Props) {
  const search = qualityApi.useSearch();
  const navigate = qualityApi.useNavigate();
  const act = useAct();
  const tenant = search.tenant ?? "";
  const tenants = useIntent({ type: "list-tenants" }, { select: toTenants });
  const answersRead = useIntent(tenant === "" ? null : { type: "tenant-answers", tenantId: tenant }, {
    select: toAnswers,
  });
  const answers = answersRead.data ?? null;
  const schema = answers === null ? "" : shownSchema(answers, search.schema);
  const listRead = useIntent(schema === "" ? null : { type: "list-evaluations", tenantId: tenant, schema }, {
    select: evaluationsOf,
    refetchInterval: (list) => (list && stillRunning(list) ? POLL_MS : false),
  });
  const list = listRead.data ?? null;
  const shownId = list === null ? null : shownEvaluation(list, search.evaluation ?? null);
  const detailRead = useIntent(shownId === null ? null : { type: "evaluation", tenantId: tenant, evaluationId: shownId }, {
    select: (body) => body as EvaluationSummary & EvaluationDetail,
  });
  const detail = detailRead.data ?? null;
  // The field whose documents are open, remembered with the evaluation it was opened on.
  const [open, setOpen] = useState<{ evaluationId: string | null; path: string } | null>(null);
  const openPath = open !== null && open.evaluationId === shownId ? open.path : null;
  const [confirming, setConfirming] = useState(false);
  const [writeProblem, setWriteProblem] = useState<string | null>(null);
  const failure = tenants.error ?? answersRead.error ?? listRead.error ?? detailRead.error;
  const problem = writeProblem ?? (failure === null ? null : describe(failure));

  const documents = answers?.find((a) => a.value === schema)?.documents ?? 0;

  const start = async () => {
    const answer = await act({ type: "start-evaluation", tenantId: tenant, schema });
    setConfirming(false);
    setWriteProblem(answer.ok ? null : describe(answer.failure));
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
          options={tenants.data ?? []}
          value={tenant}
          onChange={(chosen) => {
            setWriteProblem(null);
            void navigate({ search: qualitySearch({ tenant: chosen }) });
          }}
        />
        <SelectField
          label="Schema"
          className="w-[260px]"
          placeholder="No checked schema"
          options={answers ?? []}
          value={schema}
          onChange={(chosen) => {
            setWriteProblem(null);
            void navigate({ search: (previous) => qualitySearch({ tenant: previous.tenant, schema: chosen }) });
          }}
        />
        <Button variant="outline" disabled={schema === ""} onClick={() => void listRead.refetch()}>
          Refresh
        </Button>
        <div className="flex-1" />
        <Button disabled={schema === "" || (list !== null && stillRunning(list))} onClick={() => setConfirming(true)}>
          Re-run on checked documents
        </Button>
      </div>

      <div role="status" aria-live="polite" className="empty:-mb-3">
        {tenants.isLoading || answersRead.isLoading ? <p className="text-sm">Loading…</p> : null}
        {problem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>Quality could not be read</AlertTitle>
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        )}
        {tenant === "" && tenants.data !== undefined ? (
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
                        onClick={() => setOpen(openPath === f.path ? null : { evaluationId: shownId, path: f.path })}
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
              <Card className="py-0">
                <DataTable
                  rows={missed.documents}
                  rowKey={(d) => d.documentId}
                  label={`Documents that missed ${missed.path}`}
                  columns={[
                    {
                      key: "document",
                      header: "Document",
                      width: "200px",
                      cell: (d) => (
                        <CopyableId
                          value={d.documentId}
                          display={shortId(d.documentId)}
                          {...(d.runId === null ? {} : { onOpen: () => onOpenRun(d.runId!) })}
                        />
                      ),
                    },
                    { key: "outcome", header: "Outcome", width: "100px", cell: (d) => d.outcome },
                    { key: "expected", header: "Expected", cell: (d) => d.expected },
                    { key: "extracted", header: "Extracted", cell: (d) => d.extracted },
                  ]}
                />
              </Card>
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
                { key: "configuration", header: "Model · parser", cell: (row) => row.configuration },
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
                      onOpen={() =>
                        void navigate({ search: (previous) => qualitySearch({ ...previous, evaluation: row.evaluationId }) })
                      }
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
