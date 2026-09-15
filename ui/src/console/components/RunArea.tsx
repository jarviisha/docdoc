/**
 * The run list, and one run's detail.
 *
 * **It renders no part of a result** (FR-022a, SC-016). A succeeded run offers a
 * link to the representation the API already serves, and the console does not
 * fetch it, parse it, or derive a figure from it — `check-console-boundary.mjs`
 * fails the build if this file grows one.
 *
 * The state colour always has a word beside it. A status column that is a colour
 * alone is the first thing the operator test (SC-015) finds, and the second is a
 * filter that resets when you page.
 */

import { useEffect, useState } from "react";
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
  Selector,
  Stack,
  Table,
  Text,
  pixel,
  proportional,
} from "@astryxdesign/core";

import { RUN_STATUSES, resultHref, type Intent, type RunStatus } from "../model/client.ts";
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
  forward,
  initialListing,
  isEmpty,
  loaded,
  toPage,
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

  const cursor = currentCursor(state);
  const status = state.status;

  useEffect(() => {
    let current = true;
    void request({
      type: "list-runs",
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
  }, [request, onFailure, status, cursor]);

  if (selected !== null) {
    return <RunDetail run={selected} request={request} onBack={() => setSelected(null)} />;
  }

  const rows = state.listing.state === "loaded" ? state.listing.page.runs : [];

  return (
    <Stack direction="vertical" gap={3}>
      <HStack gap={2} align="end">
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
            aria-label="This tenant's runs, newest first"
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
  const [delivery, setDelivery] = useState<Delivery | null>(null);
  const [routing, setRouting] = useState<string | null>(null);
  const [removed, setRemoved] = useState<{ deletedAt: string; policy: string } | null>(null);

  useEffect(() => {
    void request({ type: "run-detail", runId: run.run_id }).then((answer) => {
      if (answer.ok) {
        const body = answer.body as { routing?: { outcome?: string } };
        // Absent, not empty: the route omits `routing` when no policy is
        // configured rather than sending null, and a section headed "Routing"
        // with nothing under it is a second way of saying "not configured".
        setRouting(body.routing?.outcome ?? null);
        return;
      }
      // The model classified the `410` and read the tombstone; this only shows
      // what it found (T075, FR-023).
      if (answer.failure.kind === "removed") {
        setRemoved({ deletedAt: answer.failure.deletedAt, policy: answer.failure.policy });
      }
    });

    void request({ type: "run-delivery", runId: run.run_id }).then((answer) => {
      if (answer.ok) setDelivery(toDelivery(answer.body));
    });
  }, [request, run.run_id]);

  return (
    <Stack direction="vertical" gap={3}>
      <HStack gap={2} align="center">
        <Button label="← Runs" variant="ghost" onClick={onBack} />
        <Badge variant={TONE[run.status]} label={run.status} />
      </HStack>

      <Code>{run.run_id}</Code>

      {removed === null ? null : (
        // FR-023: when, under which policy, and nothing else about what it held.
        // A tombstone carries four fields precisely so that being told about one
        // discloses nothing about what it contained.
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

      {run.processing_id === null ? null : (
        <Stack direction="vertical" gap={1}>
          <Text size="sm" weight="bold">
            Result
          </Text>
          {/* console-boundary-exempt: link only — the console renders no part of
              a result. One result representation, reachable one way. */}
          <Link href={resultHref(run.processing_id)}>Open the result this run produced</Link>
        </Stack>
      )}

      {delivery === null ? null : <DeliveryRecord delivery={delivery} />}
    </Stack>
  );
}
