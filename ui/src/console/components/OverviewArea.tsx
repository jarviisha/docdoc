/**
 * The overview: four cards split by kind, then the recent failures and the
 * runs running now — `GET /v1/console/overview`, refreshed every `POLL_MS`
 * while the tab is visible.
 *
 * **It decides nothing.** What the counts mean is `console/model/overview.ts`;
 * when to poll is `console/model/polling.ts`. A failed refresh keeps the last
 * good answer on screen and says so, rather than blanking the page.
 */

import { useCallback, useEffect, useState } from "react";
import { Banner, Button, Card, Code, HStack, Stack, Table, Text, pixel, proportional } from "@astryxdesign/core";

import type { Intent } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import { duration, shortId, when } from "../model/format.ts";
import { cardTone, toOverview, type KindSplit, type Overview, type OverviewCards } from "../model/overview.ts";
import { nextPoll, onVisible } from "../model/polling.ts";
import { kindLabel, reasonLabel, timeline, type RunSummary } from "../model/runs.ts";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  onOpenRun: (runId: string) => void;
}

/**
 * Call `refresh` every `nextPoll` while `enabled` and the tab is visible, and
 * at once when the tab becomes visible again. Shared with the run list.
 */
export function usePolling(refresh: () => void, enabled: boolean) {
  useEffect(() => {
    if (!enabled) return;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let wasHidden = document.hidden;
    const schedule = () => {
      clearTimeout(timer);
      const delay = nextPoll(document.hidden);
      if (delay === null) return;
      timer = setTimeout(() => {
        refresh();
        schedule();
      }, delay);
    };
    const visibilityChanged = () => {
      if (onVisible(wasHidden, document.hidden)) refresh();
      wasHidden = document.hidden;
      schedule();
    };
    document.addEventListener("visibilitychange", visibilityChanged);
    schedule();
    return () => {
      clearTimeout(timer);
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, [refresh, enabled]);
}

const CARDS: { name: keyof OverviewCards; label: string }[] = [
  { name: "queued", label: "Queued" },
  { name: "running", label: "Running" },
  { name: "failed", label: "Failed (last hour)" },
  { name: "done", label: "Done (last hour)" },
];

export function OverviewArea({ request, onOpenRun }: Props) {
  const [overview, setOverview] = useState<Overview | null>(null);
  // When the shown answer arrived: the "last refreshed" line, and the `now`
  // a running run's time is measured against.
  const [refreshedAt, setRefreshedAt] = useState<number | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  const refresh = useCallback(() => {
    void request({ type: "overview" }).then((answer) => {
      if (!answer.ok) {
        setProblem(describe(answer.failure));
        return;
      }
      try {
        setOverview(toOverview(answer.body));
        setRefreshedAt(Date.now());
        setProblem(null);
      } catch (error) {
        setProblem(error instanceof Error ? error.message : String(error));
      }
    });
  }, [request]);

  useEffect(refresh, [refresh]);
  usePolling(refresh, true);

  const openButton = (row: Record<string, unknown>) => (
    <Button
      label={shortId(row.run_id as string)}
      variant="ghost"
      size="sm"
      onClick={() => onOpenRun(row.run_id as string)}
    />
  );

  return (
    <Stack direction="vertical" gap={3}>
      <div role="status" aria-live="polite">
        {overview === null && problem === null ? <Text size="sm">Loading…</Text> : null}
        {problem === null ? null : (
          <Banner status="error" title="The overview could not be refreshed" description={problem} />
        )}
      </div>

      {refreshedAt === null ? null : (
        <Text size="sm" color="secondary">
          Last refreshed {when(new Date(refreshedAt).toISOString())}
        </Text>
      )}

      {overview === null ? null : (
        <>
          <HStack gap={3}>
            {CARDS.map(({ name, label }) => (
              <SplitCard
                key={name}
                label={label}
                split={overview.cards[name]}
                danger={cardTone(name, overview.cards[name]) === "danger"}
              />
            ))}
          </HStack>

          <Text weight="bold">Recent failures</Text>
          {overview.recentFailures.length === 0 ? (
            <Text size="sm">No failures in the last hour.</Text>
          ) : (
            <Card>
              <Table
                data={overview.recentFailures as unknown as Record<string, unknown>[]}
                idKey="run_id"
                density="compact"
                aria-label="Recent failures, newest first"
                columns={[
                  { key: "kind", header: "Kind", width: pixel(160), renderCell: (row) => <Text size="sm">{kindLabel(row as unknown as RunSummary)}</Text> },
                  { key: "tenant_id", header: "Tenant", width: pixel(140), renderCell: (row) => <Code>{row.tenant_id as string}</Code> },
                  {
                    key: "error_reason",
                    header: "Reason",
                    width: proportional(1),
                    renderCell: (row) => {
                      const run = row as unknown as RunSummary;
                      return <Text size="sm">{reasonLabel(run.error_reason, run.error_class) ?? "—"}</Text>;
                    },
                  },
                  { key: "finished_at", header: "Finished", width: pixel(200), renderCell: (row) => <Text size="sm">{when(row.finished_at as string | null)}</Text> },
                  { key: "run_id", header: "Run", width: pixel(140), resizable: false, renderCell: openButton },
                ]}
              />
            </Card>
          )}

          <Text weight="bold">Running now</Text>
          {overview.running.length === 0 ? (
            <Text size="sm">Nothing is running.</Text>
          ) : (
            <Card>
              <Table
                data={overview.running as unknown as Record<string, unknown>[]}
                idKey="run_id"
                density="compact"
                aria-label="Running now"
                columns={[
                  { key: "kind", header: "Kind", width: pixel(160), renderCell: (row) => <Text size="sm">{kindLabel(row as unknown as RunSummary)}</Text> },
                  { key: "tenant_id", header: "Tenant", width: pixel(140), renderCell: (row) => <Code>{row.tenant_id as string}</Code> },
                  { key: "ocr_parser", header: "Parser", width: proportional(1), renderCell: (row) => <Text size="sm">{(row.ocr_parser as string | null) ?? "—"}</Text> },
                  {
                    key: "started_at",
                    header: "Run time",
                    width: pixel(140),
                    renderCell: (row) => (
                      <Text size="sm">
                        {duration(timeline(row as unknown as RunSummary, refreshedAt ?? Date.now()).runMs)}
                      </Text>
                    ),
                  },
                  { key: "run_id", header: "Run", width: pixel(140), resizable: false, renderCell: openButton },
                ]}
              />
            </Card>
          )}
        </>
      )}
    </Stack>
  );
}

function SplitCard({ label, split, danger }: { label: string; split: KindSplit; danger: boolean }) {
  return (
    <Card padding={3} variant={danger ? "red" : "default"} width={200}>
      <Stack direction="vertical" gap={1}>
        <Text size="sm">{label}</Text>
        <Text size="lg" weight="bold">
          {split.total}
        </Text>
        <Text size="sm" color="secondary">
          OCR {split.ocr} · Extraction {split.extraction}
        </Text>
      </Stack>
    </Card>
  );
}
