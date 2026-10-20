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

import { useEffect, useState, type FormEvent } from "react";
import {
  Badge,
  Banner,
  Button,
  Card,
  Code,
  Field,
  HStack,
  MetadataList,
  MetadataListItem,
  Selector,
  Stack,
  Table,
  Text,
  pixel,
  proportional,
} from "@astryxdesign/core";

import { RUN_STATUSES, type Intent, type RunStatus } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import { toDelivery, type Delivery } from "../model/delivery.ts";
import { shortDigest, shortId, when } from "../model/format.ts";
import { DeliveryRecord } from "./DeliveryRecord.tsx";
import {
  back,
  canGoBack,
  canGoForward,
  currentCursor,
  emptyNotice,
  failed,
  filtered,
  forTenant,
  forward,
  initialListing,
  isEmpty,
  loaded,
  reloaded,
  toFailure,
  toPage,
  type RunFailure,
  type RunSummary,
} from "../model/runs.ts";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  onFailure: (failure: Failure) => void;
}

/** The state's colour, and the word that must always be beside it. */
const TONE: Record<RunStatus, "success" | "error" | "warning" | "neutral"> = {
  queued: "neutral",
  running: "warning",
  succeeded: "success",
  failed: "error",
  cancelled: "neutral",
};

export function RunArea({ request, onFailure }: Props) {
  const [state, setState] = useState(initialListing);
  const [selected, setSelected] = useState<RunSummary | null>(null);
  // What is typed, apart from what is applied: the list reloads on Enter or
  // Apply, not on every keystroke.
  const [tenantInput, setTenantInput] = useState("");

  const cursor = currentCursor(state);
  const { status, tenant } = state;
  const loading = state.listing.state === "loading";

  // Every transition that wants a page — paging, a filter, Refresh — sets the
  // listing to loading, and loading is what fetches.
  useEffect(() => {
    if (!loading) return;
    let current = true;
    void request({
      type: "list-runs",
      ...(tenant === null ? {} : { tenant }),
      ...(status === null ? {} : { status }),
      ...(cursor === undefined ? {} : { cursor }),
    }).then((answer) => {
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
  }, [request, onFailure, loading, tenant, status, cursor]);

  if (selected !== null) {
    return <RunDetail run={selected} request={request} onBack={() => setSelected(null)} />;
  }

  const rows = state.listing.state === "loaded" ? state.listing.page.runs : [];

  const applyTenant = (event: FormEvent) => {
    event.preventDefault();
    setState((previous) => forTenant(previous, tenantInput));
  };

  return (
    <Stack direction="vertical" gap={3}>
      <HStack gap={2} align="end">
        <form onSubmit={applyTenant}>
          <HStack gap={2} align="end">
            <Field label="Tenant" inputID="run-tenant">
              <input
                id="run-tenant"
                type="text"
                placeholder="Every tenant"
                value={tenantInput}
                onChange={(event) => setTenantInput(event.target.value)}
              />
            </Field>
            <Button type="submit" label="Apply" variant="secondary" />
          </HStack>
        </form>
        <Selector
          label="State"
          placeholder="Any state"
          options={[...RUN_STATUSES]}
          {...(state.status === null ? {} : { value: state.status })}
          onChange={(chosen: string) =>
            setState((previous) => filtered(previous, chosen as RunStatus))
          }
        />
        <Button
          label="All states"
          variant="ghost"
          isDisabled={state.status === null}
          onClick={() => setState((previous) => filtered(previous, null))}
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
                key: "schema_identity",
                header: "Schema",
                width: pixel(160),
                renderCell: (row) => <Code>{row.schema_identity as string}</Code>,
              },
              {
                key: "blob_id",
                header: "Document",
                width: proportional(1),
                renderCell: (row) => (
                  <Text size="sm">{shortDigest(row.blob_id as string)}</Text>
                ),
              },
              {
                key: "run_id",
                header: "Run",
                width: pixel(140),
                resizable: false,
                renderCell: (row) => (
                  <Button
                    label={shortId(row.run_id as string)}
                    variant="ghost"
                    size="sm"
                    onClick={() => setSelected(row as unknown as RunSummary)}
                  />
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
  run,
  request,
  onBack,
}: {
  run: RunSummary;
  request: Request;
  onBack: () => void;
}) {
  const [loading, setLoading] = useState(true);
  const [delivery, setDelivery] = useState<Delivery | null>(null);
  const [routing, setRouting] = useState<string | null>(null);
  const [failure, setFailure] = useState<RunFailure | null>(null);
  const [removed, setRemoved] = useState<{ deletedAt: string; policy: string } | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  /**
   * A failure worth saying out loud. `removed` has its own banner, and
   * `absent` is a run with no delivery record, which is not a fault.
   */
  const report = (answer: Failure) => {
    if (answer.kind !== "removed" && answer.kind !== "absent") setProblem(describe(answer));
  };

  useEffect(() => {
    let current = true;
    const detail = request({ type: "run-detail", runId: run.run_id }).then((answer) => {
      if (!current) return;
      if (answer.ok) {
        const body = answer.body as { routing?: { outcome?: string } };
        // Absent, not empty: the route omits `routing` when no policy is
        // configured rather than sending null, and a section headed "Routing"
        // with nothing under it is a second way of saying "not configured".
        setRouting(body.routing?.outcome ?? null);
        // Why it failed: read in the model, like the tombstone below, so the
        // requirement has coverage (US2 scenario 7, SC-015).
        setFailure(toFailure(answer.body));
        return;
      }
      // The model classified the `410` and read the tombstone; this only shows
      // what it found (T075, FR-023).
      if (answer.failure.kind === "removed") {
        setRemoved({ deletedAt: answer.failure.deletedAt, policy: answer.failure.policy });
      }
      report(answer.failure);
    });

    const record = request({ type: "run-delivery", runId: run.run_id }).then((answer) => {
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
  }, [request, run.run_id]);

  const download = async () => {
    const answer = await request({ type: "run-result", runId: run.run_id });
    if (!answer.ok) {
      // Every kind, `removed` included: here it means the result's content
      // is gone (`410 result_expired`), and a button that does nothing says less.
      setProblem(describe(answer.failure));
      return;
    }
    // `send` already parsed the body; written back out as the same JSON.
    const blob = new Blob([JSON.stringify(answer.body, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = `${run.run_id}.json`;
    anchor.click();
    URL.revokeObjectURL(url);
  };

  return (
    <Stack direction="vertical" gap={3}>
      <HStack gap={2} align="center">
        <Button label="← Runs" variant="ghost" onClick={onBack} />
        <Badge variant={TONE[run.status]} label={run.status} />
      </HStack>

      <Code>{run.run_id}</Code>

      <div role="status" aria-live="polite">
        {loading ? <Text size="sm">Loading…</Text> : null}
        {problem === null ? null : (
          <Banner status="error" title="Part of this run could not be read" description={problem} />
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

      <Card>
        <MetadataList>
          <MetadataListItem label="Tenant">
            <Code>{run.tenant_id}</Code>
          </MetadataListItem>
          <MetadataListItem label="Submitted">{when(run.created_at)}</MetadataListItem>
          <MetadataListItem label="Finished">{when(run.finished_at)}</MetadataListItem>
          <MetadataListItem label="Schema">
            <Code>{run.schema_identity}</Code>
          </MetadataListItem>
          <MetadataListItem label="Document">
            <Code>{run.blob_id}</Code>
          </MetadataListItem>
          {routing === null ? null : (
            <MetadataListItem label="Routing">{routing}</MetadataListItem>
          )}
        </MetadataList>
      </Card>

      {/* Any succeeded run has a result, OCR or extraction: the route picks. */}
      {run.status === "succeeded" ? (
        <HStack gap={2}>
          <Button label="Download result" variant="secondary" onClick={() => void download()} />
        </HStack>
      ) : null}

      {delivery === null ? null : <DeliveryRecord delivery={delivery} />}
    </Stack>
  );
}
