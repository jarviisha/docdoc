/**
 * Every parser the deployment reports, and whether it can run —
 * `GET /v1/console/parsers`, read on open and on Refresh.
 *
 * **It decides nothing.** The status and the columns' text are
 * `console/model/submit.ts`; what a failure says is `console/model/failure.ts`.
 * It never polls (`PARSERS_POLL`): a health reading probes every parser live.
 */

import { useEffect, useState } from "react";
import {
  Badge,
  Banner,
  Button,
  Card,
  Code,
  HStack,
  Stack,
  Table,
  Text,
  pixel,
  proportional,
} from "@astryxdesign/core";

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
    <Stack direction="vertical" gap={3}>
      <HStack gap={2} align="center">
        <Text size="lg" weight="bold">
          Parsers
        </Text>
        <Button
          label={parsersRefreshLabel}
          variant="secondary"
          size="sm"
          isDisabled={loading}
          onClick={() => setLoading(true)}
        />
      </HStack>

      <div role="status" aria-live="polite">
        {loading ? <Text size="sm">Loading…</Text> : null}
        {problem === null ? null : (
          <Banner status="error" title="The parsers could not be read" description={problem} />
        )}
      </div>

      {parsers.length > 0 ? (
        <Card>
          <Table
            data={parsers as unknown as Record<string, unknown>[]}
            idKey="id"
            density="compact"
            aria-label="Parsers this deployment reports"
            columns={[
              {
                key: "id",
                header: "Parser",
                width: pixel(180),
                renderCell: (row) => <Code>{row.id as string}</Code>,
              },
              {
                key: "available",
                header: "Status",
                width: proportional(1),
                renderCell: (row) => {
                  const parser = row as unknown as ParserOption;
                  const status = parserStatus(parser);
                  return (
                    <Stack direction="vertical" gap={1} align="start">
                      <Badge variant={TONE[status]} label={status} />
                      {parser.unavailableReason === null ? null : (
                        <Text size="sm" color="secondary">
                          {parser.unavailableReason}
                        </Text>
                      )}
                    </Stack>
                  );
                },
              },
              {
                key: "mediaTypes",
                header: "Media types",
                width: proportional(1),
                renderCell: (row) => <Text size="sm">{joined(row.mediaTypes as string[])}</Text>,
              },
              {
                key: "languages",
                header: "Languages",
                width: proportional(1),
                renderCell: (row) => <Text size="sm">{joined(row.languages as string[])}</Text>,
              },
              {
                key: "capabilities",
                header: "Capabilities",
                width: proportional(1),
                renderCell: (row) => (
                  <Text size="sm">{capabilityList((row as unknown as ParserOption).capabilities)}</Text>
                ),
              },
            ]}
          />
        </Card>
      ) : null}
    </Stack>
  );
}
