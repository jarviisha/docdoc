/**
 * A tenant's delivery endpoints: list, register, revoke — through
 * `/v1/console/tenants/{id}/callbacks`, which reuse the deliverer's own
 * `register` and `revoke`.
 *
 * **It decides nothing.** What a row holds, the body sent, and whether
 * Register or Revoke may be pressed are `console/model/callbacks.ts`; what a
 * failure says is `console/model/failure.ts`. The secret is typed, sent once,
 * and cleared; no answer carries it back (FR-066).
 */

import { useCallback, useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

import {
  canRegister,
  canRevoke,
  choosing,
  listFailedFor,
  listProblem,
  listedFor,
  registerBody,
  toCallbacks,
  unavailableNote,
} from "../model/callbacks.ts";
import type { Intent } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import { toTenants } from "../model/submit.ts";
import { ConfirmDialog } from "./ConfirmDialog.tsx";
import { DataTable } from "./DataTable.tsx";
import { SelectField, TextField } from "./fields.tsx";
import { SectionHeading } from "./SectionHeading.tsx";
import { Time } from "./Time.tsx";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  presetTenant: string | null;
}

export function CallbacksArea({ request, presetTenant }: Props) {
  const [tenants, setTenants] = useState<string[]>([]);
  // Whose list is shown, with its rows or the failure that answered instead.
  const [listing, setListing] = useState(() => choosing(presetTenant ?? ""));
  const { tenant, callbacks } = listing;
  // A 503: this deployment delivers no callbacks, said in place of the table.
  const unavailable = unavailableNote(listing);
  // A register or a revoke that failed; a callback-list failure is `listProblem`.
  const [writeProblem, setWriteProblem] = useState<string | null>(null);
  const problem = writeProblem ?? listProblem(listing);
  // The tenant list failed to load. Its own wording: it is not a callbacks
  // request, and reusing that title would blame the wrong request.
  const [tenantsProblem, setTenantsProblem] = useState<string | null>(null);
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [sending, setSending] = useState(false);
  // The row awaiting "Revoke this callback?", by callback id.
  const [confirming, setConfirming] = useState<string | null>(null);

  useEffect(() => {
    let current = true;
    void request({ type: "list-tenants" }).then((answer) => {
      if (!current) return;
      if (answer.ok) setTenants(toTenants(answer.body));
      else setTenantsProblem(describe(answer.failure));
    });
    return () => {
      current = false;
    };
  }, [request]);

  const load = useCallback(
    async (which: string) => {
      const answer = await request({ type: "list-callbacks", tenantId: which });
      // A late answer for a tenant no longer chosen is dropped by the model.
      if (answer.ok) setListing((previous) => listedFor(previous, which, toCallbacks(answer.body)));
      else setListing((previous) => listFailedFor(previous, which, answer.failure));
    },
    [request],
  );

  useEffect(() => {
    if (tenant !== "") void load(tenant);
  }, [tenant, load]);

  const register = () => {
    setWriteProblem(null);
    setSending(true);
    void request({ type: "register-callback", tenantId: tenant }, registerBody(url, secret)).then(
      (answer) => {
        setSending(false);
        if (!answer.ok) return setWriteProblem(describe(answer.failure));
        setSecret("");
        void load(tenant);
      },
    );
  };

  const revoke = async (callbackId: string) => {
    setWriteProblem(null);
    setSending(true);
    const answer = await request({ type: "revoke-callback", tenantId: tenant, callbackId });
    setSending(false);
    setConfirming(null);
    if (!answer.ok) setWriteProblem(describe(answer.failure));
    void load(tenant);
  };

  return (
    <div className="flex flex-col gap-3">
      <SelectField
        label="Tenant"
        placeholder="Choose a tenant"
        className="w-[280px]"
        options={tenants}
        value={tenant}
        onChange={(chosen) => {
          setWriteProblem(null);
          setConfirming(null);
          setListing(choosing(chosen));
        }}
      />

      <div role="status" aria-live="polite">
        {tenantsProblem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>Tenants could not be loaded</AlertTitle>
            <AlertDescription>{tenantsProblem}</AlertDescription>
          </Alert>
        )}
        {problem === null ? null : (
          <Alert variant="destructive">
            <AlertTitle>The callbacks request failed</AlertTitle>
            <AlertDescription>{problem}</AlertDescription>
          </Alert>
        )}
        {unavailable === null ? null : (
          <Alert variant="info">
            <AlertTitle>Callbacks are unavailable</AlertTitle>
            <AlertDescription>{unavailable}</AlertDescription>
          </Alert>
        )}
        {tenant !== "" && callbacks === null && unavailable === null && problem === null ? (
          <p className="text-sm">Loading…</p>
        ) : null}
        {callbacks !== null && callbacks.length === 0 ? (
          <Alert variant="info">
            <AlertTitle>This tenant has no callbacks registered.</AlertTitle>
          </Alert>
        ) : null}
      </div>

      {callbacks !== null && callbacks.length > 0 ? (
        <Card className="py-0">
          <DataTable
            rows={callbacks}
            rowKey={(callback) => callback.callback_id}
            label="Registered callbacks. Secrets are never shown here."
            columns={[
              {
                key: "url",
                header: "URL",
                width: "auto",
                cell: (callback) => <code className="font-mono text-xs">{callback.url}</code>,
              },
              {
                key: "created_at",
                header: "Created",
                width: "200px",
                cell: (callback) => <Time iso={callback.created_at} />,
              },
              {
                key: "revoke",
                header: "",
                width: "120px",
                cell: (callback) => (
                  <Button
                    variant="outline"
                    size="sm"
                    disabled={!canRevoke(sending)}
                    onClick={() => setConfirming(callback.callback_id)}
                  >
                    Revoke
                  </Button>
                ),
              },
            ]}
          />
        </Card>
      ) : null}

      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(open) => {
          if (!open) setConfirming(null);
        }}
        title="Revoke this callback?"
        confirmLabel="Revoke"
        cancelLabel="Keep"
        confirmDisabled={!canRevoke(sending)}
        onConfirm={() => (confirming === null ? Promise.resolve() : revoke(confirming))}
      />

      {/* Offered once the list answered: a deployment that cannot list callbacks
          (503, or no such route) cannot register one either. */}
      {callbacks !== null ? (
        <Card className="max-w-[520px]">
          <CardContent className="flex flex-col gap-3">
            <SectionHeading>Register a callback</SectionHeading>
            <TextField label="URL" value={url} onChange={setUrl} />
            <TextField
              label="Secret"
              type="password"
              autoComplete="new-password"
              value={secret}
              onChange={setSecret}
            />
            <div className="flex gap-2">
              <Button disabled={!canRegister(url, secret, sending)} onClick={register}>
                {sending ? "Registering…" : "Register"}
              </Button>
            </div>
          </CardContent>
        </Card>
      ) : null}
    </div>
  );
}
