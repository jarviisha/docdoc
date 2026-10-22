/**
 * Every parser the deployment reports, and whether it can run —
 * `GET /v1/console/parsers`, read on open and on Refresh.
 *
 * **It decides nothing.** The status and the columns' text are
 * `console/model/submit.ts`; what a failure says is `console/model/failure.ts`.
 * It never polls (`PARSERS_POLL`): a health reading probes every parser live.
 */

import { useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import type { Intent } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import { joined } from "../model/format.ts";
import {
  capabilityList,
  parsersRefreshLabel,
  parserStatus,
  toParsers,
  type ParserOption,
} from "../model/submit.ts";
import { DataTable } from "./DataTable.tsx";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
}

/** The status's colour, and the word that is always beside it. */
const TONE: Record<ReturnType<typeof parserStatus>, "success" | "error" | "warning" | "neutral"> = {
  healthy: "success",
  "not responding": "warning",
  unavailable: "error",
  unknown: "neutral",
};

export function ParsersArea({ request }: Props) {
  const [parsers, setParsers] = useState<ParserOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [problem, setProblem] = useState<string | null>(null);

  // Loading is what fetches: the first render and every Refresh set it.
  useEffect(() => {
    if (!loading) return;
    let current = true;
    void request({ type: "list-parsers" }).then((answer) => {
      if (!current) return;
      setLoading(false);
      if (answer.ok) {
        setParsers(toParsers(answer.body));
        setProblem(null);
      } else {
        setProblem(describe(answer.failure));
      }
    });
    return () => {
      current = false;
    };
  }, [request, loading]);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center gap-2">
        <Button variant="outline" size="sm" disabled={loading} onClick={() => setLoading(true)}>
          {parsersRefreshLabel}
        </Button>
      </div>

      <div role="status" aria-live="polite">
        {loading ? <p className="text-sm">Loading…</p> : null}
        {problem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>The parsers could not be read</AlertTitle>
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        )}
      </div>

      {parsers.length > 0 ? (
        <Card className="py-0">
          <DataTable
            rows={parsers}
            rowKey={(parser) => parser.id}
            label="Parsers this deployment reports"
            columns={[
              {
                key: "id",
                header: "Parser",
                width: "180px",
                cell: (parser) => <code className="font-mono text-xs">{parser.id}</code>,
              },
              {
                key: "available",
                header: "Status",
                width: "auto",
                cell: (parser) => {
                  const status = parserStatus(parser);
                  return (
                    <div className="flex flex-col items-start gap-1">
                      <Badge variant={TONE[status]}>{status}</Badge>
                      {parser.unavailableReason === null ? null : (
                        <p className="text-sm text-muted-foreground">{parser.unavailableReason}</p>
                      )}
                    </div>
                  );
                },
              },
              {
                key: "mediaTypes",
                header: "Media types",
                width: "auto",
                cell: (parser) => <span className="text-sm">{joined(parser.mediaTypes)}</span>,
              },
              {
                key: "languages",
                header: "Languages",
                width: "auto",
                cell: (parser) => <span className="text-sm">{joined(parser.languages)}</span>,
              },
              {
                key: "capabilities",
                header: "Capabilities",
                width: "auto",
                cell: (parser) => <span className="text-sm">{capabilityList(parser.capabilities)}</span>,
              },
            ]}
          />
        </Card>
      ) : null}
    </div>
  );
}
