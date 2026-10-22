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
import { Card } from "@/components/ui/card";

import type { Intent } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import { duration, shortId, when } from "../model/format.ts";
import { toOverview, type Overview } from "../model/overview.ts";
import { isNewer } from "../model/polling.ts";
import { kindLabel, reasonLabel, timeline, type RunSummary } from "../model/runs.ts";
import { CopyableId } from "./CopyableId.tsx";
import { DataTable } from "./DataTable.tsx";
import { SectionHeading } from "./SectionHeading.tsx";
import { Time } from "./Time.tsx";
import { StatStrip } from "./StatStrip.tsx";
import { usePolling } from "./usePolling.ts";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  onOpenRun: (runId: string) => void;
}

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
    <CopyableId value={run.run_id} display={shortId(run.run_id)} onOpen={() => onOpenRun(run.run_id)} />
  );

  return (
    <div className="flex flex-col gap-3">
      <div role="status" aria-live="polite" className="empty:-mb-3">
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
          <StatStrip cards={overview.cards} />

          <SectionHeading>Recent failures</SectionHeading>
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
                  { key: "finished_at", header: "Finished", width: "200px", cell: (run) => <Time iso={run.finished_at} /> },
                  { key: "run_id", header: "Run", width: "140px", cell: openButton },
                ]}
              />
            </Card>
          )}

          <SectionHeading>Running now</SectionHeading>
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
                    align: "right",
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
