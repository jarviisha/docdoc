/**
 * Every tenant the deployment knows — `GET /v1/console/tenants` — each with
 * its own jumps into Runs, Credentials, Callbacks and Erasure, scoped to it.
 *
 * **It decides nothing.** Which jumps a row offers, and where each lands, are
 * `console/model/navigation.ts`.
 */

import { useEffect, useState } from "react";
import {
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
import { TENANT_JUMPS, jumpTo, type Jump } from "../model/navigation.ts";
import { toTenants } from "../model/submit.ts";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  onJump: (jump: Jump) => void;
}

export function TenantsArea({ request, onJump }: Props) {
  const [tenants, setTenants] = useState<string[] | null>(null);
  const [problem, setProblem] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    void request({ type: "list-tenants" }).then((answer) => {
      if (!current) return;
      if (answer.ok) setTenants(toTenants(answer.body));
      else {
        setTenants([]);
        setProblem(describe(answer.failure));
      }
    });
    return () => {
      current = false;
    };
  }, [request]);

  return (
    <Stack direction="vertical" gap={3}>
      <Text size="lg" weight="bold">
        Tenants
      </Text>

      <div role="status" aria-live="polite">
        {tenants === null ? <Text size="sm">Loading…</Text> : null}
        {problem === null ? null : (
          <Banner status="error" title="The tenants could not be read" description={problem} />
        )}
        {tenants !== null && tenants.length === 0 && problem === null ? (
          <Banner status="info" title="This deployment has no tenants yet." />
        ) : null}
      </div>

      {tenants !== null && tenants.length > 0 ? (
        <Card>
          <Table
            data={tenants.map((tenant) => ({ tenant }))}
            idKey="tenant"
            hasHover
            density="compact"
            aria-label="Tenants"
            columns={[
              {
                key: "tenant",
                header: "Tenant",
                width: proportional(1),
                renderCell: (row) => <Code>{row.tenant as string}</Code>,
              },
              {
                key: "jumps",
                header: "",
                width: pixel(420),
                resizable: false,
                renderCell: (row) => (
                  <HStack gap={2}>
                    {TENANT_JUMPS.map(({ area, label }) => (
                      <Button
                        key={area}
                        label={label}
                        variant="secondary"
                        size="sm"
                        onClick={() => onJump(jumpTo(area, row.tenant as string))}
                      />
                    ))}
                  </HStack>
                ),
              },
            ]}
          />
        </Card>
      ) : null}
    </Stack>
  );
}
