/**
 * The run list across every tenant, and one run's detail.
 *
 * A succeeded run's result can be downloaded as the JSON the deployment
 * serves (ADR-0023 §4): fetched through `run-result` and handed to the
 * browser as a file, re-serialised but otherwise untouched.
 *
 * The state colour always has a word beside it. A status column that is a colour
 * alone is the first thing the operator test (SC-015) finds, and the second is a
 * filter that resets when you page.
 */

import { useMemo, useState, type ReactNode } from "react";
import { getRouteApi, Link } from "@tanstack/react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button, buttonVariants } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

import { RUN_STATUSES, type RunStatus } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import { toDelivery } from "../model/delivery.ts";
import { duration, shortId, when } from "../model/format.ts";
import { POLL_MS } from "../model/polling.ts";
import { runsSearch } from "../model/search.ts";
import { toResultPages, type ResultPage } from "../model/result.ts";
import { checkBody, fieldPages, reportIdOf, saveFailure, toFieldRows, toSave, type Draft } from "../model/fields.ts";
import { FieldCheckTable } from "./FieldCheckTable.tsx";
import { ConfirmDialog } from "./ConfirmDialog.tsx";
import { CopyableId } from "./CopyableId.tsx";
import { DataTable } from "./DataTable.tsx";
import { DeliveryRecord } from "./DeliveryRecord.tsx";
import { SelectField, TextField } from "./fields.tsx";
import { LiveStatStrip } from "./StatStrip.tsx";
import { Time } from "./Time.tsx";
import { ResultViewer } from "./ResultViewer.tsx";
import type { ImageRequest, Request } from "./RequestContext.tsx";
import { useAct, useIntent } from "./useIntent.ts";
import {
  autoRefreshes,
  back,
  canGoBack,
  currentCursor,
  emptyNotice,
  forward,
  cancelNotice,
  detailPolls,
  isCancellable,
  kindLabel,
  listIntent,
  reasonLabel,
  timeline,
  toFailure,
  toPage,
  refetchAfterCancel,
  resultNotice,
  showsFields,
  showsResult,
  toRecord,
  type RunFilters,
  type RunKind,
} from "../model/runs.ts";

/** The run list's URL: its filters are the search params, so Back and a shared link restore them. */
const runsApi = getRouteApi("/runs");

/** The state's colour, and the word that must always be beside it. */
const TONE: Record<RunStatus, "success" | "error" | "warning" | "neutral" | "info"> = {
  queued: "neutral",
  running: "info",
  succeeded: "success",
  failed: "error",
  cancelled: "neutral",
};

/** The "no filter" option. Not a run kind or a state the model knows. */
const ALL = "all";

const KINDS: { value: RunKind; label: string }[] = [
  { value: "ocr", label: "OCR" },
  { value: "extraction", label: "Extraction" },
];

/**
 * The list under the URL's filters. The route keys this by those filters, so
 * a filter change mounts it afresh on page one: the cursor stack is local and
 * never carried into a different ordering.
 */
export function RunArea({ onOpenRun }: { onOpenRun: (runId: string) => void }) {
  const filters = runsApi.useSearch();
  const navigate = runsApi.useNavigate();
  const [stack, setStack] = useState<string[]>([]);
  // What is typed, apart from what is applied: the list reloads on Enter or
  // Apply, not on every keystroke.
  const [tenantInput, setTenantInput] = useState(filters.tenant ?? "");

  // Rows stay while the next page loads, and a failed background refresh keeps
  // them and says so; only a page that never arrived reads as a failure.
  const list = useIntent(listIntent(filters, currentCursor(stack)), {
    select: toPage,
    keepPrevious: true,
    refetchInterval: autoRefreshes(stack) ? POLL_MS : false,
  });
  const page = list.data;
  const rows = page?.runs ?? [];

  /**
   * Push a history entry with one filter changed: Back undoes it. Parsed as
   * the URL is, so a blank or `undefined` filter leaves the URL.
   */
  const setFilter = (change: { [K in keyof RunFilters]?: RunFilters[K] | undefined }) =>
    void navigate({ search: (previous) => runsSearch({ ...previous, ...change }) });

  const applyTenant = () => setFilter({ tenant: tenantInput });

  return (
    <div className="flex flex-col gap-3">
      <LiveStatStrip />

      {/* One row, bottom-aligned: the labels sit above and the boxes and
          buttons share a baseline. */}
      <div className="flex flex-wrap items-end gap-2">
        <TextField
          label="Tenant"
          placeholder="Every tenant"
          className="w-[220px]"
          value={tenantInput}
          onChange={setTenantInput}
          onEnter={applyTenant}
        />
        <Button variant="outline" onClick={applyTenant}>
          Apply
        </Button>
        <SelectField
          label="Kind"
          className="w-[160px]"
          options={[{ value: ALL, label: "All" }, ...KINDS]}
          value={filters.kind ?? ALL}
          onChange={(chosen) => setFilter({ kind: chosen === ALL ? undefined : (chosen as RunKind) })}
        />
        <SelectField
          label="State"
          className="w-[160px]"
          options={[{ value: ALL, label: "All" }, ...RUN_STATUSES]}
          value={filters.status ?? ALL}
          onChange={(chosen) => setFilter({ status: chosen === ALL ? undefined : (chosen as RunStatus) })}
        />
        <Button variant="outline" disabled={list.isPending} onClick={() => void list.refetch()}>
          Refresh
        </Button>
        <div className="flex-1" />
        <Link to="/submit" className={buttonVariants()}>
          Submit
        </Link>
      </div>

      {/* FR-045b: a list loading, and a list that failed, reach a screen reader
          rather than only the eye. `polite` and not `assertive` — an operator
          reading a row should not be interrupted by a page they asked for. */}
      <div role="status" aria-live="polite">
        {list.isPending ? <p className="text-sm">Loading…</p> : null}

        {list.isError && page === undefined ? (
          // Not an empty list. An empty tenant and a refused request read
          // identically if this branch does not exist (Edge Cases).
          <Alert variant="destructive">
            <AlertTitle>The run list could not be read</AlertTitle>
            <AlertDescription>{describe(list.error)}</AlertDescription>
          </Alert>
        ) : null}

        {page !== undefined && rows.length === 0 ? (
          <Alert variant="info">
            <AlertTitle>{emptyNotice(filters)}</AlertTitle>
          </Alert>
        ) : null}

        {list.isError && page !== undefined ? (
          <Alert variant="warning">
            <AlertTitle>The run list could not be refreshed</AlertTitle>
            <AlertDescription>{describe(list.error)}</AlertDescription>
          </Alert>
        ) : null}
      </div>
      {rows.length > 0 ? (
        <Card className="py-0">
          <DataTable
            rows={rows}
            rowKey={(run) => run.run_id}
            label="Runs, newest first"
            columns={[
              {
                key: "status",
                header: "State",
                width: "120px",
                // The word is the label; the colour is decoration beside it.
                cell: (run) => <Badge variant={TONE[run.status]}>{run.status}</Badge>,
              },
              {
                key: "kind",
                header: "Kind",
                width: "160px",
                cell: (run) => <span className="text-sm">{kindLabel(run)}</span>,
              },
              {
                key: "tenant_id",
                header: "Tenant",
                width: "140px",
                cell: (run) => <code className="font-mono text-xs">{run.tenant_id}</code>,
              },
              {
                key: "created_at",
                header: "Submitted",
                width: "200px",
                cell: (run) => <Time iso={run.created_at} />,
              },
              {
                key: "started_at",
                header: "Duration",
                width: "140px",
                align: "right",
                cell: (run) => <span className="text-sm">{duration(timeline(run, Date.now()).runMs)}</span>,
              },
              {
                key: "run_id",
                header: "Run",
                width: "140px",
                cell: (run) => (
                  // No `href`: a `<button>` styled as a link.
                  <CopyableId value={run.run_id} display={shortId(run.run_id)} onOpen={() => onOpenRun(run.run_id)} />
                ),
              },
            ]}
          />
        </Card>
      ) : null}

      <div className="flex gap-2">
        <Button variant="outline" disabled={!canGoBack(stack)} onClick={() => setStack(back)}>
          Previous
        </Button>
        {/* Not while the last page's rows stand in: their cursor is the one already used. */}
        <Button
          variant="outline"
          disabled={page?.next_cursor == null || list.isPlaceholderData}
          onClick={() => setStack((previous) => forward(previous, page?.next_cursor ?? null))}
        >
          Next
        </Button>
      </div>
    </div>
  );
}

export function RunDetail({
  runId,
  request,
  requestImage,
}: {
  runId: string;
  request: Request;
  requestImage: ImageRequest;
}) {
  const act = useAct();
  const [confirming, setConfirming] = useState(false);
  const [cancelProblem, setCancelProblem] = useState<string | null>(null);
  const [cancelAccepted, setCancelAccepted] = useState(false);
  const [downloadProblem, setDownloadProblem] = useState<string | null>(null);
  // The operator's unsaved marks, and the hovered field.
  const [drafts, setDrafts] = useState<Record<string, Draft>>({});
  const [hoveredField, setHoveredField] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveProblem, setSaveProblem] = useState<string | null>(null);

  // While the run can still change, read it again. A failed re-read leaves the
  // run shown as last read — unless the run is gone (`410`), which no later
  // read will undo, so polling stops.
  const detail = useIntent(
    { type: "run-detail", runId },
    {
      select: toRecord,
      refetchInterval: (shown, error) => (error?.kind !== "removed" && detailPolls(shown ?? null) ? POLL_MS : false),
    },
  );
  const delivery = useIntent({ type: "run-delivery", runId }, { select: toDelivery });

  // The model classified the `410` and read the tombstone; this only shows
  // what it found (T075, FR-023).
  const removed = detail.error?.kind === "removed" ? detail.error : null;
  const run = removed === null ? (detail.data ?? null) : null;
  // `toRecord` keeps the body's other fields. Absent, not empty: the route
  // omits `routing` when no policy is configured rather than sending null,
  // and a section headed "Routing" with nothing under it is a second way of
  // saying "not configured".
  const routing = (run as { routing?: { outcome?: string } } | null)?.routing?.outcome ?? null;
  // Why it failed: read in the model, like the tombstone, so the requirement
  // has coverage (US2 scenario 7, SC-015).
  const failure = toFailure(run);

  // Read the first time the run is seen to have a result to show.
  const hasFields = showsFields(run);
  const hasResult = showsResult(run);
  const result = useIntent(hasResult ? { type: "run-result", runId } : null);
  // An extraction run's checks, read again after every save (`record-check` makes them stale).
  const checks = useIntent(hasFields ? { type: "run-checks", runId } : null);

  const pages = useMemo((): { pages: ResultPage[] } | { problem: string } | null => {
    if (hasFields || result.data === undefined) return null;
    try {
      return { pages: toResultPages(result.data) };
    } catch (error) {
      return { problem: error instanceof Error ? error.message : "the result could not be read" };
    }
  }, [hasFields, result.data]);
  const resultProblem: ReturnType<typeof resultNotice> | null =
    result.error !== null
      ? resultNotice(result.error)
      : pages !== null && "problem" in pages
        ? { status: "error", title: "The result could not be shown", description: pages.problem }
        : null;

  /**
   * A failure worth saying out loud. `removed` has its own banner, and
   * `absent` is a run with no delivery record, which is not a fault.
   */
  const report = (answer: Failure | null) =>
    answer === null || answer.kind === "removed" || answer.kind === "absent" ? null : describe(answer);
  const problem =
    report(detail.error) ?? report(delivery.error) ?? (checks.error === null ? null : describe(checks.error)) ?? downloadProblem;
  const loading = detail.isPending || delivery.isPending;

  const rows =
    !hasFields || result.data === undefined || checks.data === undefined ? null : toFieldRows(result.data, checks.data);
  const hoveredRow = rows?.find((row) => row.path === hoveredField) ?? null;

  /** One `record-check` per changed row; a mark whose save failed stays to be saved again. */
  const save = async () => {
    if (rows === null) return;
    setSaving(true);
    const kept: Record<string, Draft> = {};
    const failed: { path: string; problem: string }[] = [];
    for (const row of toSave(rows, drafts)) {
      const draft = drafts[row.path]!;
      const body = checkBody(row, draft.verdict, draft, reportIdOf(result.data));
      const answer = await act({ type: "record-check", runId }, JSON.stringify(body));
      if (!answer.ok) {
        kept[row.path] = draft;
        failed.push({ path: row.path, problem: describe(answer.failure) });
      }
    }
    setDrafts(kept);
    setSaveProblem(saveFailure(failed));
    setSaving(false);
  };

  const download = async () => {
    const answer = await request({ type: "run-result", runId });
    if (!answer.ok) {
      // Every kind: an expired result (`410 result_expired`) arrives as a
      // `reported` failure saying so, and a button that does nothing says less.
      setDownloadProblem(describe(answer.failure));
      return;
    }
    setDownloadProblem(null);
    // `send` already parsed the body; written back out as the same JSON.
    const blob = new Blob([JSON.stringify(answer.body, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${runId}.json`;
    anchor.click();
    // Revoked on the next tick: revoking synchronously can cancel the download
    // before the browser has started reading the blob.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  };

  /** A success makes the run stale and it is read again; a `409` says why not. */
  const cancel = async () => {
    const answer = await act({ type: "cancel-run", runId });
    setConfirming(false);
    if (answer.ok) {
      setCancelProblem(null);
      setCancelAccepted(true);
    } else {
      setCancelProblem(describe(answer.failure));
      setCancelAccepted(false);
      if (refetchAfterCancel(answer.failure)) void detail.refetch();
    }
  };

  const times = run === null ? null : timeline(run, Date.now());
  const pending = cancelAccepted && run !== null ? cancelNotice(run) : null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        {/* `exact`: the router would otherwise mark it the current page, since a run's path is under it. */}
        <Link to="/runs" activeOptions={{ exact: true }} className={buttonVariants({ variant: "ghost" })}>
          ← Runs
        </Link>
        {run === null ? null : <Badge variant={TONE[run.status]}>{run.status}</Badge>}
      </div>

      <CopyableId value={runId} />

      <div role="status" aria-live="polite">
        {loading ? <p className="text-sm">Loading…</p> : null}
        {problem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>Part of this run could not be read</AlertTitle>
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        )}
        {cancelProblem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>The run could not be cancelled</AlertTitle>
            <AlertDescription>{cancelProblem}</AlertDescription>
          </Alert>
        )}
        {pending === null ? null : (
          <Alert variant="info">
            <AlertTitle>{pending}</AlertTitle>
          </Alert>
        )}
        {saveProblem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>Some checks could not be saved</AlertTitle>
            <AlertDescription>{saveProblem}</AlertDescription>
          </Alert>
        )}
        {resultProblem === null ? null : (
          <Alert variant={resultProblem.status === "error" ? "destructive" : "info"}>
            <AlertTitle>{resultProblem.title}</AlertTitle>
            {resultProblem.description === undefined ? null : (
              <AlertDescription>{resultProblem.description}</AlertDescription>
            )}
          </Alert>
        )}
      </div>

      {failure === null ? null : (
        // US2 scenario 7, FR-039. A class name and a stage, exactly as the
        // deployment produced them — no message, because the route sends none
        // and a provider's message may quote the document it choked on.
        <div role="status" aria-live="polite">
          <Alert variant="destructive">
            <AlertTitle>
              {failure.stage === null
                ? "This run failed before any stage ran"
                : `This run failed at the ${failure.stage} stage`}
            </AlertTitle>
            <AlertDescription>{failure.errorClass}</AlertDescription>
          </Alert>
        </div>
      )}

      {removed === null ? null : (
        // FR-023: when, under which policy, and nothing else about what it held.
        <div role="status" aria-live="polite">
          <Alert variant="info">
            <AlertTitle>This run was removed</AlertTitle>
            <AlertDescription>
              {removed.deletedAt === ""
                ? "It is gone, and only its removal is recorded."
                : `Removed on ${when(removed.deletedAt)} under the ${removed.policy} policy. Nothing else about it is recorded.`}
            </AlertDescription>
          </Alert>
        </div>
      )}

      {run === null || times === null ? null : (
        <>
          <p className="text-sm">
            Submitted <Time iso={run.created_at} /> → Started <Time iso={run.started_at} /> (wait{" "}
            {duration(times.waitMs)}) → Finished <Time iso={run.finished_at} /> (run{" "}
            {duration(times.runMs)})
          </p>

          <Card>
            <CardContent>
              <dl className="grid grid-cols-[max-content_1fr] gap-x-6 gap-y-1 text-sm">
                <Meta label="Tenant">
                  <code className="font-mono text-xs">{run.tenant_id}</code>
                </Meta>
                <Meta label="Kind">{kindLabel(run)}</Meta>
                {run.kind === "ocr" ? (
                  <>
                    <Meta label="Parser">{run.ocr_parser ?? "—"}</Meta>
                    <Meta label="Language">{run.ocr_language ?? "—"}</Meta>
                  </>
                ) : null}
                <Meta label="Worker">{run.worker_id ?? "—"}</Meta>
                <Meta label="Attempts">{run.attempts ?? "—"}</Meta>
                <Meta label="Error class">{run.error_class ?? "—"}</Meta>
                <Meta label="Failure reason">{reasonLabel(run.error_reason, run.error_class) ?? "—"}</Meta>
                {run.schema_identity === null ? null : (
                  <Meta label="Schema">
                    <code className="font-mono text-xs">{run.schema_identity}</code>
                  </Meta>
                )}
                <Meta label="Document">
                  <CopyableId value={run.blob_id} />
                </Meta>
                {routing === null ? null : <Meta label="Routing">{routing}</Meta>}
              </dl>
            </CardContent>
          </Card>

          {isCancellable(run.status) ? (
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => setConfirming(true)}>
                Cancel run
              </Button>
            </div>
          ) : null}

          {/* Closes itself if polling sees the run finish while it is open:
              there is nothing left to cancel. */}
          <ConfirmDialog
            open={confirming && isCancellable(run.status)}
            onOpenChange={setConfirming}
            title="Cancel this run?"
            confirmLabel="Cancel run"
            cancelLabel="Keep running"
            onConfirm={cancel}
          />

          {/* Any succeeded run has a result, OCR or extraction: the route picks. */}
          {run.status === "succeeded" ? (
            <div className="flex gap-2">
              <Button variant="outline" onClick={() => void download()}>
                Download result
              </Button>
            </div>
          ) : null}

          {pages === null || !("pages" in pages) ? null : (
            <ResultViewer runId={runId} pages={pages.pages} request={requestImage} />
          )}

          {rows === null ? null : (
            <ResultViewer
              runId={runId}
              pages={fieldPages(rows)}
              request={requestImage}
              highlight={hoveredRow?.location ?? null}
              aside={
                <FieldCheckTable
                  rows={rows}
                  drafts={drafts}
                  onDraft={(path, draft) =>
                    setDrafts((previous) => {
                      const next = { ...previous };
                      if (draft === null) delete next[path];
                      else next[path] = draft;
                      return next;
                    })
                  }
                  hovered={hoveredField}
                  onHover={setHoveredField}
                  saving={saving}
                  onSave={() => void save()}
                />
              }
            />
          )}
        </>
      )}

      {delivery.data === undefined ? null : <DeliveryRecord delivery={delivery.data} />}
    </div>
  );
}

function Meta({ label, children }: { label: string; children: ReactNode }) {
  return (
    <>
      <dt className="text-muted-foreground">{label}</dt>
      <dd>{children}</dd>
    </>
  );
}
