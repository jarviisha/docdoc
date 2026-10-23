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

import { useState } from "react";
import { getRouteApi } from "@tanstack/react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";

import {
  canRegister,
  canRevoke,
  listProblem,
  registerBody,
  toCallbacks,
  unavailableNote,
} from "../model/callbacks.ts";
import { describe } from "../model/failure.ts";
import { tenantSearch } from "../model/search.ts";
import { toTenants } from "../model/submit.ts";
import { ConfirmDialog } from "./ConfirmDialog.tsx";
import { DataTable } from "./DataTable.tsx";
import { SelectField, TextField } from "./fields.tsx";
import { SectionHeading } from "./SectionHeading.tsx";
import { Time } from "./Time.tsx";
import { useAct, useIntent } from "./useIntent.ts";

const callbacksApi = getRouteApi("/callbacks");

/** The tenant in the URL is the one listed; choosing one pushes it, so Back returns to the last. */
export function CallbacksArea() {
  const tenant = callbacksApi.useSearch().tenant ?? "";
  const navigate = callbacksApi.useNavigate();
  const act = useAct();
  const tenants = useIntent({ type: "list-tenants" }, { select: toTenants });
  const list = useIntent(tenant === "" ? null : { type: "list-callbacks", tenantId: tenant }, {
    select: toCallbacks,
  });
  const callbacks = list.data ?? null;
  // A 503: this deployment delivers no callbacks, said in place of the table.
  const unavailable = unavailableNote(list.error);
  // A register or a revoke that failed; a callback-list failure is `listProblem`.
  const [writeProblem, setWriteProblem] = useState<string | null>(null);
  const problem = writeProblem ?? listProblem(list.error);
  // The tenant list failed to load. Its own wording: it is not a callbacks
  // request, and reusing that title would blame the wrong request.
  const tenantsProblem = tenants.isError ? describe(tenants.error) : null;
  const [url, setUrl] = useState("");
  const [secret, setSecret] = useState("");
  const [sending, setSending] = useState(false);
  // The row awaiting "Revoke this callback?", by callback id.
  const [confirming, setConfirming] = useState<string | null>(null);

  const register = () => {
    setWriteProblem(null);
    setSending(true);
    void act({ type: "register-callback", tenantId: tenant }, registerBody(url, secret)).then((answer) => {
      setSending(false);
      if (!answer.ok) return setWriteProblem(describe(answer.failure));
      setSecret("");
    });
  };

  const revoke = async (callbackId: string) => {
    setWriteProblem(null);
    setSending(true);
    const answer = await act({ type: "revoke-callback", tenantId: tenant, callbackId });
    setSending(false);
    setConfirming(null);
    if (!answer.ok) setWriteProblem(describe(answer.failure));
  };

  return (
    <div className="flex flex-col gap-3">
      <SelectField
        label="Tenant"
        placeholder="Choose a tenant"
        className="w-[280px]"
        options={tenants.data ?? []}
        value={tenant}
        onChange={(chosen) => {
          setWriteProblem(null);
          setConfirming(null);
          void navigate({ search: (previous) => tenantSearch({ ...previous, tenant: chosen }) });
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
