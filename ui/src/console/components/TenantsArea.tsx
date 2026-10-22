/**
 * Every tenant the deployment knows — `GET /v1/console/tenants` — each with
 * its own jumps into Runs, Credentials, Callbacks and Erasure, scoped to it.
 *
 * **It decides nothing.** Which jumps a row offers, and where each lands, are
 * `console/model/navigation.ts`.
 */

import { useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import type { Intent } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import { TENANT_JUMPS, jumpTo, type Jump } from "../model/navigation.ts";
import { toTenants } from "../model/submit.ts";
import { DataTable } from "./DataTable.tsx";

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
    <div className="flex flex-col gap-3">
      <div role="status" aria-live="polite">
        {tenants === null ? <p className="text-sm">Loading…</p> : null}
        {problem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>The tenants could not be read</AlertTitle>
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        )}
        {tenants !== null && tenants.length === 0 && problem === null ? (
          <Alert variant="info">
            <AlertTitle>This deployment has no tenants yet.</AlertTitle>
          </Alert>
        ) : null}
      </div>

      {tenants !== null && tenants.length > 0 ? (
        <Card className="py-0">
          <DataTable
            rows={tenants}
            rowKey={(tenant) => tenant}
            label="Tenants"
            columns={[
              {
                key: "tenant",
                header: "Tenant",
                width: "auto",
                cell: (tenant) => <code className="font-mono text-xs">{tenant}</code>,
              },
              {
                key: "jumps",
                header: "",
                width: "420px",
                cell: (tenant) => (
                  <div className="flex gap-2">
                    {TENANT_JUMPS.map(({ area, label }) => (
                      <Button key={area} variant="outline" size="sm" onClick={() => onJump(jumpTo(area, tenant))}>
                        {label}
                      </Button>
                    ))}
                  </div>
                ),
              },
            ]}
          />
        </Card>
      ) : null}
    </div>
  );
}
