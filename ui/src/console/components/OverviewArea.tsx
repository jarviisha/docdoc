/**
 * The overview: four cards split by kind, then the recent failures and the
 * runs running now — `GET /v1/console/overview`, refreshed every `POLL_MS`
 * while the tab is visible.
 *
 * **It decides nothing.** What the counts mean is `console/model/overview.ts`;
 * when to poll is `console/model/polling.ts`. A failed refresh keeps the last
 * good answer on screen and says so, rather than blanking the page.
 */

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Card } from "@/components/ui/card";

import { describe } from "../model/failure.ts";
import { duration, shortId, when } from "../model/format.ts";
import { toOverview } from "../model/overview.ts";
import { POLL_MS } from "../model/polling.ts";
import { kindLabel, reasonLabel, timeline, type RunSummary } from "../model/runs.ts";
import { CopyableId } from "./CopyableId.tsx";
import { DataTable } from "./DataTable.tsx";
import { SectionHeading } from "./SectionHeading.tsx";
import { Time } from "./Time.tsx";
import { StatStrip } from "./StatStrip.tsx";
import { useIntent } from "./useIntent.ts";

interface Props {
  onOpenRun: (runId: string) => void;
}

/** `toOverview` throws on a body it cannot read; a query's error is a `Failure`, so say it as one. */
function readOverview(body: unknown) {
  try {
    return toOverview(body);
  } catch (error) {
    throw { kind: "transport", message: error instanceof Error ? error.message : String(error) };
  }
}

export function OverviewArea({ onOpenRun }: Props) {
  // The last good answer stays on screen while a refresh fails (`keepPrevious`
  // only covers a changed key; a failed refetch keeps `data` by itself).
  const { data: overview, error, dataUpdatedAt } = useIntent(
    { type: "overview" },
    { select: readOverview, refetchInterval: POLL_MS, keepPrevious: true },
  );
  const problem = error === null ? null : describe(error);
  // When the shown answer arrived: the "last refreshed" line, and the `now`
  // a running run's time is measured against.
  const refreshedAt = overview === undefined ? null : dataUpdatedAt;

  // No `href`: a `<button>` styled as a link.
  const openButton = (run: RunSummary) => (
    <CopyableId value={run.run_id} display={shortId(run.run_id)} onOpen={() => onOpenRun(run.run_id)} />
  );

  return (
    <div className="flex flex-col gap-3">
      <div role="status" aria-live="polite" className="empty:-mb-3">
        {overview === undefined && problem === null ? <p className="text-sm">Loading…</p> : null}
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

      {overview === undefined ? null : (
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
