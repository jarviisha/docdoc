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

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

import { RUN_STATUSES, type Intent, type RunStatus } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import { toDelivery, type Delivery } from "../model/delivery.ts";
import { duration, shortId, when } from "../model/format.ts";
import { toResultPages, type ResultPage } from "../model/result.ts";
import { ConfirmDialog } from "./ConfirmDialog.tsx";
import { DataTable } from "./DataTable.tsx";
import { DeliveryRecord } from "./DeliveryRecord.tsx";
import { SelectField, TextField } from "./fields.tsx";
import { ResultViewer, type ImageRequest } from "./ResultViewer.tsx";
import { usePolling } from "./usePolling.ts";
import {
  autoRefreshes,
  back,
  byKind,
  canGoBack,
  canGoForward,
  currentCursor,
  emptyNotice,
  failed,
  filtered,
  forTenant,
  forward,
  initialListing,
  cancelNotice,
  detailPolls,
  isCancellable,
  isEmpty,
  kindLabel,
  loaded,
  polled,
  reasonLabel,
  reloaded,
  timeline,
  toFailure,
  toPage,
  refetchAfterCancel,
  resultNotice,
  showsResult,
  toRecord,
  type ListingState,
  type RunFailure,
  type RunKind,
  type RunRecord,
  type RunSummary,
} from "../model/runs.ts";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  /** `request` for a page image, so the session ending is still the shell's to act on. */
  requestImage: ImageRequest;
  onFailure: (failure: Failure) => void;
  /** The run whose detail is shown, held by the shell so the overview can open one. */
  openRunId: string | null;
  onOpenRun: (runId: string | null) => void;
  /** The tenant a jump from Tenants opened this area for, applied as the filter on mount. */
  presetTenant: string | null;
}

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

/** The request for the listing's current page and filters. */
function listIntent(
  state: Pick<ListingState, "tenant" | "status" | "kind">,
  cursor: string | undefined,
): Intent {
  return {
    type: "list-runs",
    ...(state.tenant === null ? {} : { tenant: state.tenant }),
    ...(state.status === null ? {} : { status: state.status }),
    ...(state.kind === null ? {} : { kind: state.kind }),
    ...(cursor === undefined ? {} : { cursor }),
  };
}

export function RunArea({
  request,
  requestImage,
  onFailure,
  openRunId,
  onOpenRun,
  presetTenant,
}: Props) {
  const [state, setState] = useState(() => forTenant(initialListing(), presetTenant ?? ""));
  // What is typed, apart from what is applied: the list reloads on Enter or
  // Apply, not on every keystroke.
  const [tenantInput, setTenantInput] = useState(presetTenant ?? "");
  // A failed background refresh, said here and cleared by the next success:
  // a notice in the shell would outlive the failure it reports.
  const [pollProblem, setPollProblem] = useState<string | null>(null);

  const cursor = currentCursor(state);
  const { status, tenant, kind } = state;
  const loading = state.listing.state === "loading";

  // Every transition that wants a page — paging, a filter, Refresh — sets the
  // listing to loading, and loading is what fetches.
  useEffect(() => {
    if (!loading) return;
    let current = true;
    void request(listIntent({ tenant, status, kind }, cursor)).then((answer) => {
      if (!current) return;
      if (answer.ok) {
        setState((previous) => loaded(previous, toPage(answer.body)));
      } else {
        setState((previous) => failed(previous, answer.failure));
        onFailure(answer.failure);
      }
    });
    return () => {
      current = false;
    };
  }, [request, onFailure, loading, tenant, status, kind, cursor]);

  // A background refresh replaces page one in place, without the loading
  // state, so rows are never blanked under the reader. A failed one keeps the
  // rows and says so locally, as the overview does.
  const poll = useCallback(async () => {
    const askedFor = state;
    const answer = await request(listIntent(askedFor, currentCursor(askedFor)));
    if (answer.ok) {
      setState((previous) => polled(previous, askedFor, toPage(answer.body)));
      setPollProblem(null);
    } else {
      setPollProblem(describe(answer.failure));
    }
  }, [request, state]);
  usePolling(poll, autoRefreshes(state) && openRunId === null);

  if (openRunId !== null) {
    return (
      <RunDetail
        runId={openRunId}
        request={request}
        requestImage={requestImage}
        onBack={() => onOpenRun(null)}
      />
    );
  }

  const rows = state.listing.state === "loaded" ? state.listing.page.runs : [];

  const applyTenant = () => setState((previous) => forTenant(previous, tenantInput));

  return (
    <div className="flex flex-col gap-3">
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
          value={state.kind ?? ALL}
          onChange={(chosen) =>
            setState((previous) => byKind(previous, chosen === ALL ? null : (chosen as RunKind)))
          }
        />
        <SelectField
          label="State"
          className="w-[160px]"
          options={[{ value: ALL, label: "All" }, ...RUN_STATUSES]}
          value={state.status ?? ALL}
          onChange={(chosen) =>
            setState((previous) => filtered(previous, chosen === ALL ? null : (chosen as RunStatus)))
          }
        />
        <Button variant="outline" disabled={loading} onClick={() => setState(reloaded)}>
          Refresh
        </Button>
      </div>

      {/* FR-045b: a list loading, and a list that failed, reach a screen reader
          rather than only the eye. `polite` and not `assertive` — an operator
          reading a row should not be interrupted by a page they asked for. */}
      <div role="status" aria-live="polite">
        {state.listing.state === "loading" ? <p className="text-sm">Loading…</p> : null}

        {state.listing.state === "failed" ? (
          // Not an empty list. An empty tenant and a refused request read
          // identically if this branch does not exist (Edge Cases).
          <Alert variant="destructive">
            <AlertTitle>The run list could not be read</AlertTitle>
            <AlertDescription>{describe(state.listing.failure)}</AlertDescription>
          </Alert>
        ) : null}

        {isEmpty(state) ? (
          <Alert variant="info">
            <AlertTitle>{emptyNotice(state)}</AlertTitle>
          </Alert>
        ) : null}

        {pollProblem === null ? null : (
          <Alert variant="warning">
            <AlertTitle>The run list could not be refreshed</AlertTitle>
            <AlertDescription>{pollProblem}</AlertDescription>
          </Alert>
        )}
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
                cell: (run) => <span className="text-sm">{when(run.created_at)}</span>,
              },
              {
                key: "started_at",
                header: "Duration",
                width: "140px",
                cell: (run) => <span className="text-sm">{duration(timeline(run, Date.now()).runMs)}</span>,
              },
              {
                key: "run_id",
                header: "Run",
                width: "140px",
                cell: (run) => (
                  // No `href`: a `<button>` styled as a link.
                  <Button variant="link" className="h-auto p-0" onClick={() => onOpenRun(run.run_id)}>
                    {shortId(run.run_id)}
                  </Button>
                ),
              },
            ]}
          />
        </Card>
      ) : null}

      <div className="flex gap-2">
        <Button variant="outline" disabled={!canGoBack(state)} onClick={() => setState(back)}>
          Previous
        </Button>
        <Button variant="outline" disabled={!canGoForward(state)} onClick={() => setState(forward)}>
          Next
        </Button>
      </div>
    </div>
  );
}

function RunDetail({
  runId,
  request,
  requestImage,
  onBack,
}: {
  runId: string;
  request: Request;
  requestImage: ImageRequest;
  onBack: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [run, setRun] = useState<RunRecord | null>(null);
  const [delivery, setDelivery] = useState<Delivery | null>(null);
  const [routing, setRouting] = useState<string | null>(null);
  const [failure, setFailure] = useState<RunFailure | null>(null);
  const [removed, setRemoved] = useState<{ deletedAt: string; policy: string } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
  const [confirming, setConfirming] = useState(false);
  const [cancelProblem, setCancelProblem] = useState<string | null>(null);
  const [cancelAccepted, setCancelAccepted] = useState(false);
  const [pages, setPages] = useState<ResultPage[] | null>(null);
  const [resultProblem, setResultProblem] = useState<ReturnType<typeof resultNotice> | null>(null);
  // Bumped to read the run again, e.g. after a `409` says the one shown is stale.
  const [reads, setReads] = useState(0);

  /**
   * A failure worth saying out loud. `removed` has its own banner, and
   * `absent` is a run with no delivery record, which is not a fault.
   */
  const report = (answer: Failure) => {
    if (answer.kind !== "removed" && answer.kind !== "absent") setProblem(describe(answer));
  };

  /** Show a `run-detail` answer: the run, its routing, and why it failed. */
  const showDetail = useCallback((body: unknown) => {
    setRun(toRecord(body));
    // Absent, not empty: the route omits `routing` when no policy is
    // configured rather than sending null, and a section headed "Routing"
    // with nothing under it is a second way of saying "not configured".
    setRouting((body as { routing?: { outcome?: string } }).routing?.outcome ?? null);
    // Why it failed: read in the model, like the tombstone below, so the
    // requirement has coverage (US2 scenario 7, SC-015).
    setFailure(toFailure(body));
  }, []);

  useEffect(() => {
    let current = true;
    const detail = request({ type: "run-detail", runId }).then((answer) => {
      if (!current) return;
      if (answer.ok) {
        showDetail(answer.body);
        return;
      }
      // The model classified the `410` and read the tombstone; this only shows
      // what it found (T075, FR-023).
      if (answer.failure.kind === "removed") {
        setRemoved({ deletedAt: answer.failure.deletedAt, policy: answer.failure.policy });
      }
      report(answer.failure);
    });

    const record = request({ type: "run-delivery", runId }).then((answer) => {
      if (!current) return;
      if (answer.ok) setDelivery(toDelivery(answer.body));
      else report(answer.failure);
    });

    void Promise.all([detail, record]).then(() => {
      if (current) setLoading(false);
    });
    return () => {
      current = false;
    };
  }, [request, runId, reads, showDetail]);

  // While the run can still change, read it again. A failed re-read is left
  // for the next one: the run shown is still the last one read — unless the
  // run is gone (`410`), which no later read will undo, so polling stops.
  const pollDetail = useCallback(async () => {
    const answer = await request({ type: "run-detail", runId });
    if (answer.ok) return showDetail(answer.body);
    if (answer.failure.kind === "removed") {
      setRun(null);
      setRemoved({ deletedAt: answer.failure.deletedAt, policy: answer.failure.policy });
    }
  }, [request, runId, showDetail]);
  usePolling(pollDetail, detailPolls(run));

  // Read once, the first time the run is seen to have a result to show.
  const hasResult = showsResult(run);
  useEffect(() => {
    if (!hasResult) return;
    let current = true;
    void request({ type: "run-result", runId }).then((answer) => {
      if (!current) return;
      if (!answer.ok) {
        setResultProblem(resultNotice(answer.failure));
        return;
      }
      try {
        setPages(toResultPages(answer.body));
      } catch (error) {
        setResultProblem({
          status: "error",
          title: "The result could not be shown",
          description: error instanceof Error ? error.message : "the result could not be read",
        });
      }
    });
    return () => {
      current = false;
    };
  }, [request, runId, hasResult]);

  const download = async () => {
    const answer = await request({ type: "run-result", runId });
    if (!answer.ok) {
      // Every kind: an expired result (`410 result_expired`) arrives as a
      // `reported` failure saying so, and a button that does nothing says less.
      setProblem(describe(answer.failure));
      return;
    }
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

  /** The answer is the run as it now stands, and replaces the one shown; a `409` says why not. */
  const cancel = async () => {
    const answer = await request({ type: "cancel-run", runId });
    setConfirming(false);
    if (answer.ok) {
      setRun(toRecord(answer.body));
      setCancelProblem(null);
      setCancelAccepted(true);
    } else {
      setCancelProblem(describe(answer.failure));
      setCancelAccepted(false);
      if (refetchAfterCancel(answer.failure)) setReads((n) => n + 1);
    }
  };

  const times = run === null ? null : timeline(run, Date.now());
  const pending = cancelAccepted && run !== null ? cancelNotice(run) : null;

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Button variant="ghost" onClick={onBack}>
          ← Runs
        </Button>
        {run === null ? null : <Badge variant={TONE[run.status]}>{run.status}</Badge>}
      </div>

      <code className="font-mono text-xs">{runId}</code>

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
            Submitted {when(run.created_at)} → Started {when(run.started_at)} (wait{" "}
            {duration(times.waitMs)}) → Finished {when(run.finished_at)} (run{" "}
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
                  <code className="font-mono text-xs">{run.blob_id}</code>
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

          {pages === null ? null : (
            <ResultViewer runId={runId} pages={pages} request={requestImage} />
          )}
        </>
      )}

      {delivery === null ? null : <DeliveryRecord delivery={delivery} />}
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
