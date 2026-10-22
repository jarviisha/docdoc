/**
 * The overview: four cards split by kind, then the recent failures and the
 * runs running now — `GET /v1/console/overview`, refreshed every `POLL_MS`
 * while the tab is visible.
 *
 * **It decides nothing.** What the counts mean is `console/model/overview.ts`;
 * when to poll is `console/model/polling.ts`. A failed refresh keeps the last
 * good answer on screen and says so, rather than blanking the page.
 */

import { useCallback, useEffect, useRef, useState } from "react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

import type { Intent } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import { duration, shortId, when } from "../model/format.ts";
import { cardTone, toOverview, type KindSplit, type Overview, type OverviewCards } from "../model/overview.ts";
import { isNewer } from "../model/polling.ts";
import { kindLabel, reasonLabel, timeline, type RunSummary } from "../model/runs.ts";
import { DataTable } from "./DataTable.tsx";
import { usePolling } from "./usePolling.ts";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  onOpenRun: (runId: string) => void;
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

  // Requests are numbered as they are sent; only an answer newer than the one
  // shown is applied, so a slow answer arriving late cannot put older data back.
  const sent = useRef(0);
  const shown = useRef<number | null>(null);

  const refresh = useCallback(async () => {
    const number = ++sent.current;
    const answer = await request({ type: "overview" });
    if (!isNewer(number, shown.current)) return;
    if (!answer.ok) {
      setProblem(describe(answer.failure));
      return;
    }
    try {
      setOverview(toOverview(answer.body));
      shown.current = number;
      setRefreshedAt(Date.now());
      setProblem(null);
    } catch (error) {
      setProblem(error instanceof Error ? error.message : String(error));
    }
  }, [request]);

  useEffect(() => {
    void refresh();
  }, [refresh]);
  usePolling(refresh, true);

  // No `href`: a `<button>` styled as a link.
  const openButton = (run: RunSummary) => (
    <Button variant="link" className="h-auto p-0" onClick={() => onOpenRun(run.run_id)}>
      {shortId(run.run_id)}
    </Button>
  );

  return (
    <div className="flex flex-col gap-3">
      <div role="status" aria-live="polite">
        {overview === null && problem === null ? <p className="text-sm">Loading…</p> : null}
        {problem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>The overview could not be refreshed</AlertTitle>
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        )}
      </div>

      {refreshedAt === null ? null : (
        <p className="text-sm text-muted-foreground">
          Last refreshed {when(new Date(refreshedAt).toISOString())}
        </p>
      )}

      {overview === null ? null : (
        <>
          <div className="flex flex-wrap gap-3">
            {CARDS.map(({ name, label }) => (
              <SplitCard
                key={name}
                label={label}
                split={overview.cards[name]}
                danger={cardTone(name, overview.cards[name]) === "danger"}
              />
            ))}
          </div>

          <p className="font-bold">Recent failures</p>
          {overview.recentFailures.length === 0 ? (
            <p className="text-sm">No failures.</p>
          ) : (
            <Card className="py-0">
              <DataTable
                rows={overview.recentFailures}
                rowKey={(run) => run.run_id}
                label="Recent failures, newest first"
                columns={[
                  { key: "kind", header: "Kind", width: "160px", cell: (run) => <span className="text-sm">{kindLabel(run)}</span> },
                  { key: "tenant_id", header: "Tenant", width: "140px", cell: (run) => <code className="font-mono text-xs">{run.tenant_id}</code> },
                  {
                    key: "error_reason",
                    header: "Reason",
                    width: "auto",
                    cell: (run) => <span className="text-sm">{reasonLabel(run.error_reason, run.error_class) ?? "—"}</span>,
                  },
                  { key: "finished_at", header: "Finished", width: "200px", cell: (run) => <span className="text-sm">{when(run.finished_at)}</span> },
                  { key: "run_id", header: "Run", width: "140px", cell: openButton },
                ]}
              />
            </Card>
          )}

          <p className="font-bold">Running now</p>
          {overview.running.length === 0 ? (
            <p className="text-sm">Nothing is running.</p>
          ) : (
            <Card className="py-0">
              <DataTable
                rows={overview.running}
                rowKey={(run) => run.run_id}
                label="Running now"
                columns={[
                  { key: "kind", header: "Kind", width: "160px", cell: (run) => <span className="text-sm">{kindLabel(run)}</span> },
                  { key: "tenant_id", header: "Tenant", width: "140px", cell: (run) => <code className="font-mono text-xs">{run.tenant_id}</code> },
                  { key: "ocr_parser", header: "Parser", width: "auto", cell: (run) => <span className="text-sm">{run.ocr_parser ?? "—"}</span> },
                  {
                    key: "started_at",
                    header: "Run time",
                    width: "140px",
                    cell: (run) => (
                      <span className="text-sm">{duration(timeline(run, refreshedAt ?? Date.now()).runMs)}</span>
                    ),
                  },
                  { key: "run_id", header: "Run", width: "140px", cell: openButton },
                ]}
              />
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function SplitCard({ label, split, danger }: { label: string; split: KindSplit; danger: boolean }) {
  return (
    <Card className={cn("w-[200px] py-3", danger && "border-red-300 bg-red-50")}>
      <CardContent className="flex flex-col gap-1 px-3">
        <p className="text-sm">{label}</p>
        <p className="text-lg font-bold">{split.total}</p>
        <p className="text-sm text-muted-foreground">
          OCR {split.ocr} · Extraction {split.extraction}
        </p>
      </CardContent>
    </Card>
  );
}
