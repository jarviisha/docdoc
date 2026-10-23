/**
 * Every schema the deployment knows, one row per name — `GET /v1/console/schemas`,
 * read on open and on Refresh.
 *
 * **It decides nothing.** Grouping, the source's wording and the rows read are
 * `console/model/schemas.ts`; what a failure says is `console/model/failure.ts`.
 */

import { Link } from "@tanstack/react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import { describe } from "../model/failure.ts";
import { groupByName, sourceLabel, toSchemaEntries } from "../model/schemas.ts";
import { DataTable } from "./DataTable.tsx";
import { useIntent } from "./useIntent.ts";

const LINK = "font-mono text-xs text-primary underline-offset-2 hover:underline";

export function SchemasArea() {
  const { data, error, isFetching, refetch } = useIntent({ type: "list-schemas" }, { select: toSchemaEntries });
  const groups = groupByName(data ?? []);
  const problem = error === null ? null : describe(error);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-end gap-2">
        <Button variant="outline" size="sm" disabled={isFetching} onClick={() => void refetch()}>
          Refresh
        </Button>
        <span className="flex-1" />
        <Button asChild>
          <Link to="/schemas/new">New schema</Link>
        </Button>
      </div>

      <div role="status" aria-live="polite" className="empty:-mb-3">
        {isFetching && data === undefined ? <p className="text-sm">Loading…</p> : null}
        {problem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>The schemas could not be read</AlertTitle>
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        )}
        {data !== undefined && groups.length === 0 ? (
          <Alert variant="info">
            <AlertTitle>This deployment has no schemas yet. Use New schema to author one.</AlertTitle>
          </Alert>
        ) : null}
      </div>

      {groups.length > 0 ? (
        <Card className="py-0">
          <DataTable
            rows={groups}
            rowKey={(group) => group.name}
            label="Schemas, one row per name"
            columns={[
              {
                key: "name",
                header: "Name",
                width: "180px",
                cell: (group) => <code className="font-mono text-xs">{group.name}</code>,
              },
              {
                key: "versions",
                header: "Versions",
                width: "auto",
                cell: (group) => (
                  <div className="flex flex-wrap items-center gap-2">
                    {group.versions.map((entry) => (
                      <span key={entry.identity} className="flex items-center gap-1">
                        <Link to="/schemas/$identity" params={{ identity: entry.identity }} className={LINK}>
                          @{entry.version}
                        </Link>
                        {entry.shadowsFile ? (
                          <Badge variant="warning">a file with this identity is ignored</Badge>
                        ) : null}
                      </span>
                    ))}
                  </div>
                ),
              },
              {
                key: "latest",
                header: "Latest",
                width: "140px",
                cell: (group) => <code className="font-mono text-xs">{group.latest.identity}</code>,
              },
              {
                key: "source",
                header: "Source",
                width: "auto",
                cell: (group) => <span className="text-xs text-slate-600">{sourceLabel(group.latest)}</span>,
              },
              {
                key: "fields",
                header: "Fields",
                width: "80px",
                align: "right",
                cell: (group) => group.latest.fieldCount,
              },
              {
                key: "new",
                header: "",
                width: "120px",
                cell: (group) => (
                  <Button asChild variant="outline" size="sm">
                    <Link to="/schemas/new" search={{ base: group.latest.identity }}>
                      New version
                    </Link>
                  </Button>
                ),
              },
            ]}
          />
        </Card>
      ) : null}
    </div>
  );
}
