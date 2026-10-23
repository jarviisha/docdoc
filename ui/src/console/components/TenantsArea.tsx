/**
 * Every tenant the deployment knows — `GET /v1/console/tenants` — each with
 * its own jumps into Runs, Credentials, Callbacks and Erasure, scoped to it.
 *
 * **It decides nothing.** Which jumps a row offers, and where each lands, are
 * `console/model/navigation.ts`.
 */

import { Link } from "@tanstack/react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { buttonVariants } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import { describe } from "../model/failure.ts";
import { TENANT_JUMPS, jumpTo } from "../model/navigation.ts";
import { toTenants } from "../model/submit.ts";
import { DataTable } from "./DataTable.tsx";
import { useIntent } from "./useIntent.ts";

export function TenantsArea() {
  const { data, error } = useIntent({ type: "list-tenants" }, { select: toTenants });
  const problem = error === null ? null : describe(error);
  // A failed read shows as no tenants, with the failure beside it.
  const tenants = data ?? (error === null ? null : []);

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
                      <Link key={area} {...jumpTo(area, tenant)} className={buttonVariants({ variant: "outline", size: "sm" })}>
                        {label}
                      </Link>
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
