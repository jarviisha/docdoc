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

import { useCallback, useEffect, useState } from "react";
import {
  Badge,
  Banner,
  Button,
  Card,
  Code,
  HStack,
  Link,
  MetadataList,
  MetadataListItem,
  Stack,
  Selector,
  Table,
  Text,
  TextInput,
  pixel,
} from "@astryxdesign/core";

import { RUN_STATUSES, type Intent, type RunStatus } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import { toDelivery, type Delivery } from "../model/delivery.ts";
import { duration, shortId, when } from "../model/format.ts";
import { toResultPages, type ResultPage } from "../model/result.ts";
import { DeliveryRecord } from "./DeliveryRecord.tsx";
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
}

/** The state's colour, and the word that must always be beside it. */
const TONE: Record<RunStatus, "success" | "error" | "warning" | "neutral"> = {
  queued: "neutral",
  running: "warning",
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

export function RunArea({ request, requestImage, onFailure, openRunId, onOpenRun }: Props) {
  const [state, setState] = useState(initialListing);
  // What is typed, apart from what is applied: the list reloads on Enter or
  // Apply, not on every keystroke.
  const [tenantInput, setTenantInput] = useState("");
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
    <Stack direction="vertical" gap={3}>
      {/* One row, bottom-aligned: every control is the same `md` height, so
          the labels sit above and the boxes and buttons share a baseline. */}
      <HStack gap={2} align="end" wrap="wrap">
        <TextInput
          label="Tenant"
          placeholder="Every tenant"
          width={220}
          value={tenantInput}
          onChange={setTenantInput}
          onEnter={applyTenant}
        />
        <Button label="Apply" variant="secondary" onClick={applyTenant} />
        <Selector
          label="Kind"
          width={160}
          options={[{ value: ALL, label: "All" }, ...KINDS]}
          value={state.kind ?? ALL}
          onChange={(chosen) =>
            setState((previous) => byKind(previous, chosen === ALL ? null : (chosen as RunKind)))
          }
        />
        <Selector
          label="State"
          width={160}
          options={[{ value: ALL, label: "All" }, ...RUN_STATUSES]}
          value={state.status ?? ALL}
          onChange={(chosen) =>
            setState((previous) => filtered(previous, chosen === ALL ? null : (chosen as RunStatus)))
          }
        />
        <Button
          label="Refresh"
          variant="secondary"
          isDisabled={loading}
          onClick={() => setState(reloaded)}
        />
      </HStack>

      {/* FR-045b: a list loading, and a list that failed, reach a screen reader
          rather than only the eye. `polite` and not `assertive` — an operator
          reading a row should not be interrupted by a page they asked for. */}
      <div role="status" aria-live="polite">
        {state.listing.state === "loading" ? <Text size="sm">Loading…</Text> : null}

        {state.listing.state === "failed" ? (
          // Not an empty list. An empty tenant and a refused request read
          // identically if this branch does not exist (Edge Cases).
          <Banner
            status="error"
            title="The run list could not be read"
            description={describe(state.listing.failure)}
          />
        ) : null}

        {isEmpty(state) ? <Banner status="info" title={emptyNotice(state)} /> : null}

        {pollProblem === null ? null : (
          <Banner status="warning" title="The run list could not be refreshed" description={pollProblem} />
        )}
      </div>

      {rows.length > 0 ? (
        <Card>
          <Table
            data={rows as unknown as Record<string, unknown>[]}
            idKey="run_id"
            hasHover
            density="compact"
            aria-label="Runs, newest first"
            columns={[
              {
                key: "status",
                header: "State",
                width: pixel(120),
                renderCell: (row) => (
                  // The word is the label; the colour is decoration beside it.
                  <Badge variant={TONE[row.status as RunStatus]} label={row.status as string} />
                ),
              },
              {
                key: "kind",
                header: "Kind",
                width: pixel(160),
                renderCell: (row) => (
                  <Text size="sm">{kindLabel(row as unknown as RunSummary)}</Text>
                ),
              },
              {
                key: "tenant_id",
                header: "Tenant",
                width: pixel(140),
                renderCell: (row) => <Code>{row.tenant_id as string}</Code>,
              },
              {
                key: "created_at",
                header: "Submitted",
                width: pixel(200),
                renderCell: (row) => <Text size="sm">{when(row.created_at as string)}</Text>,
              },
              {
                key: "started_at",
                header: "Duration",
                width: pixel(140),
                renderCell: (row) => (
                  <Text size="sm">
                    {duration(timeline(row as unknown as RunSummary, Date.now()).runMs)}
                  </Text>
                ),
              },
              {
                key: "run_id",
                header: "Run",
                width: pixel(140),
                resizable: false,
                renderCell: (row) => (
                  // No `href`: a `<button>` styled as a link.
                  <Link hasUnderline onClick={() => onOpenRun(row.run_id as string)}>
                    {shortId(row.run_id as string)}
                  </Link>
                ),
              },
            ]}
          />
        </Card>
      ) : null}

      <HStack gap={2}>
        <Button
          label="Previous"
          variant="secondary"
          isDisabled={!canGoBack(state)}
          onClick={() => setState(back)}
        />
        <Button
          label="Next"
          variant="secondary"
          isDisabled={!canGoForward(state)}
          onClick={() => setState(forward)}
        />
      </HStack>
    </Stack>
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
  const [cancelling, setCancelling] = useState(false);
  const [cancelProblem, setCancelProblem] = useState<string | null>(null);
  const [cancelAccepted, setCancelAccepted] = useState(false);
  const [pages, setPages] = useState<ResultPage[] | null>(null);
  const [resultProblem, setResultProblem] = useState<string | null>(null);
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
        setResultProblem(describe(answer.failure));
        return;
      }
      try {
        setPages(toResultPages(answer.body));
      } catch (error) {
        setResultProblem(error instanceof Error ? error.message : "the result could not be read");
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
    setCancelling(true);
    const answer = await request({ type: "cancel-run", runId });
    setCancelling(false);
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
    <Stack direction="vertical" gap={3}>
      <HStack gap={2} align="center">
        <Button label="← Runs" variant="ghost" onClick={onBack} />
        {run === null ? null : <Badge variant={TONE[run.status]} label={run.status} />}
      </HStack>

      <Code>{runId}</Code>

      <div role="status" aria-live="polite">
        {loading ? <Text size="sm">Loading…</Text> : null}
        {problem === null ? null : (
          <Banner status="error" title="Part of this run could not be read" description={problem} />
        )}
        {cancelProblem === null ? null : (
          <Banner status="error" title="The run could not be cancelled" description={cancelProblem} />
        )}
        {pending === null ? null : <Banner status="info" title={pending} />}
        {resultProblem === null ? null : (
          <Banner status="error" title="The result could not be shown" description={resultProblem} />
        )}
      </div>

      {failure === null ? null : (
        // US2 scenario 7, FR-039. A class name and a stage, exactly as the
        // deployment produced them — no message, because the route sends none
        // and a provider's message may quote the document it choked on.
        <div role="status" aria-live="polite">
          <Banner
            status="error"
            title={
              failure.stage === null
                ? "This run failed before any stage ran"
                : `This run failed at the ${failure.stage} stage`
            }
            description={failure.errorClass}
          />
        </div>
      )}

      {removed === null ? null : (
        // FR-023: when, under which policy, and nothing else about what it held.
        <div role="status" aria-live="polite">
          <Banner
            status="info"
            title="This run was removed"
            description={
              removed.deletedAt === ""
                ? "It is gone, and only its removal is recorded."
                : `Removed on ${when(removed.deletedAt)} under the ${removed.policy} policy. Nothing else about it is recorded.`
            }
          />
        </div>
      )}

      {run === null || times === null ? null : (
        <>
          <Text size="sm">
            Submitted {when(run.created_at)} → Started {when(run.started_at)} (wait{" "}
            {duration(times.waitMs)}) → Finished {when(run.finished_at)} (run{" "}
            {duration(times.runMs)})
          </Text>

          <Card>
            <MetadataList>
              <MetadataListItem label="Tenant">
                <Code>{run.tenant_id}</Code>
              </MetadataListItem>
              <MetadataListItem label="Kind">{kindLabel(run)}</MetadataListItem>
              {run.kind === "ocr" ? (
                <>
                  <MetadataListItem label="Parser">{run.ocr_parser ?? "—"}</MetadataListItem>
                  <MetadataListItem label="Language">{run.ocr_language ?? "—"}</MetadataListItem>
                </>
              ) : null}
              <MetadataListItem label="Worker">{run.worker_id ?? "—"}</MetadataListItem>
              <MetadataListItem label="Attempts">{run.attempts ?? "—"}</MetadataListItem>
              <MetadataListItem label="Error class">{run.error_class ?? "—"}</MetadataListItem>
              <MetadataListItem label="Failure reason">
                {reasonLabel(run.error_reason, run.error_class) ?? "—"}
              </MetadataListItem>
              {run.schema_identity === null ? null : (
                <MetadataListItem label="Schema">
                  <Code>{run.schema_identity}</Code>
                </MetadataListItem>
              )}
              <MetadataListItem label="Document">
                <Code>{run.blob_id}</Code>
              </MetadataListItem>
              {routing === null ? null : (
                <MetadataListItem label="Routing">{routing}</MetadataListItem>
              )}
            </MetadataList>
          </Card>

          {isCancellable(run.status) ? (
            confirming ? (
              <HStack gap={2} align="center">
                <Text size="sm">Cancel this run?</Text>
                <Button
                  label="Confirm"
                  variant="primary"
                  isLoading={cancelling}
                  isDisabled={cancelling}
                  onClick={() => void cancel()}
                />
                <Button
                  label="Keep running"
                  variant="secondary"
                  isDisabled={cancelling}
                  onClick={() => setConfirming(false)}
                />
              </HStack>
            ) : (
              <HStack gap={2}>
                <Button label="Cancel run" variant="secondary" onClick={() => setConfirming(true)} />
              </HStack>
            )
          ) : null}

          {/* Any succeeded run has a result, OCR or extraction: the route picks. */}
          {run.status === "succeeded" ? (
            <HStack gap={2}>
              <Button label="Download result" variant="secondary" onClick={() => void download()} />
            </HStack>
          ) : null}

          {pages === null ? null : (
            <ResultViewer runId={runId} pages={pages} request={requestImage} />
          )}
        </>
      )}

      {delivery === null ? null : <DeliveryRecord delivery={delivery} />}
    </Stack>
  );
}
