/**
 * Issue, list, revoke — through `/v1/console/credentials` (ADR-0023 §3).
 *
 * The key appears once, with the sentence saying so, and is gone on
 * acknowledgement (FR-024). No row in the listing carries one (FR-025). A
 * revocation is confirmed in its own row before it is sent, because it
 * cannot be undone and the button sits beside every active key.
 */

import { useCallback, useEffect, useState } from "react";
import { Alert, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import type { Intent } from "../model/client.ts";
import {
  DISCLOSURE_WARNING,
  SELF_REVOCATION_WARNING,
  acknowledged,
  disclosureFrom,
  issueBody,
  isActive,
  listedFor,
  listFailedFor,
  listingNote,
  needsWarning,
  presetCredentials,
  scopesOf,
  showing,
  toRecords,
  warned,
  withDisclosure,
} from "../model/credentials.ts";
import type { CredentialRecord } from "../model/credentials.ts";
import type { Failure } from "../model/failure.ts";
import { shortId, when } from "../model/format.ts";
import { ConfirmDialog } from "./ConfirmDialog.tsx";
import { DataTable } from "./DataTable.tsx";
import { TextField } from "./fields.tsx";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  onFailure: (failure: Failure) => void;
  /** The tenant a jump from Tenants opened this area for: preset, then shown. */
  presetTenant: string | null;
}

export function CredentialArea({ request, onFailure, presetTenant }: Props) {
  const [state, setState] = useState(() => presetCredentials(presetTenant));
  const [tenant, setTenant] = useState(presetTenant ?? "");
  const [label, setLabel] = useState("");

  // Which tenant's credentials to list: typed, then shown on Show — the route
  // requires one, and listing on every keystroke asks about tenants nobody meant.
  const [listing, setListing] = useState(presetTenant ?? "");
  const shown = state.shown;
  // The row awaiting "Revoke this key?", by credential id.
  const [confirming, setConfirming] = useState<string | null>(null);

  const load = useCallback(
    async (which: string) => {
      if (which === "") return;
      const answer = await request({ type: "list-credentials", tenant: which });
      if (answer.ok) setState((previous) => listedFor(previous, which, toRecords(answer.body)));
      else {
        setState((previous) => listFailedFor(previous, which));
        onFailure(answer.failure);
      }
    },
    [request, onFailure],
  );

  // A jump's own Show: the listing is already pending for the preset tenant.
  // `load` ignores "", so a sidebar visit asks for nothing.
  useEffect(() => {
    void load(presetTenant ?? "");
  }, [load, presetTenant]);

  const revoke = async (credentialId: string) => {
    // The warning shows before the first revocation and the action still
    // proceeds: a leaked key is very often the one somebody is still using.
    setState(warned);
    const answer = await request({ type: "revoke-credential", credentialId });
    setConfirming(null);
    if (!answer.ok) onFailure(answer.failure);
    void load(shown);
  };

  return (
    <div className="flex flex-col gap-3">
      {state.disclosure === null ? null : (
        <div className="flex flex-col gap-2" role="status" aria-live="assertive">
          {/* FR-045b: the key is shown once. A reader who does not see the
              screen has exactly one chance to be told that. */}
          <Alert variant="warning">
            <AlertTitle>{DISCLOSURE_WARNING}</AlertTitle>
          </Alert>
          <TextField label="The issued key" readOnly value={state.disclosure.key} />
          <div className="flex gap-2">
            <Button onClick={() => setState(acknowledged)}>I have copied it</Button>
          </div>
        </div>
      )}

      <div className="flex max-w-[480px] flex-col gap-2">
        <p className="text-sm font-bold">Issue a credential</p>
        <TextField label="Tenant" value={tenant} onChange={setTenant} />
        <TextField label="Label (what it is for)" value={label} onChange={setLabel} />
        <div className="flex gap-2">
          <Button
            disabled={tenant.trim() === ""}
            onClick={() => {
              void request({ type: "issue-credential" }, issueBody(tenant.trim(), label)).then(
                (answer) => {
                  if (!answer.ok) return onFailure(answer.failure);
                  setState((previous) => withDisclosure(previous, disclosureFrom(answer.body)));
                  void load(shown);
                },
              );
            }}
          >
            Issue
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <p className="text-sm font-bold">Credentials</p>

        <div className="flex items-end gap-2">
          <TextField
            label="List credentials for tenant"
            className="w-[280px]"
            value={listing}
            onChange={setListing}
          />
          <Button
            variant="outline"
            disabled={listing.trim() === ""}
            onClick={() => {
              const which = listing.trim();
              setConfirming(null);
              setState((previous) => showing(previous, which));
              void load(which);
            }}
          >
            Show
          </Button>
        </div>

        {needsWarning(state) ? (
          <Alert variant="info">
            <AlertTitle>{SELF_REVOCATION_WARNING}</AlertTitle>
          </Alert>
        ) : null}

        {listingNote(state) !== null ? (
          <p className="text-sm">{listingNote(state)}</p>
        ) : (
          <Card className="py-0">
            <DataTable
              rows={state.records}
              rowKey={(record) => record.credential_id}
              label="Issued credentials. Keys are never shown here."
              columns={[
                {
                  key: "credential_id",
                  header: "Credential",
                  width: "auto",
                  cell: (record) => <code className="font-mono text-xs">{shortId(record.credential_id)}</code>,
                },
                {
                  key: "label",
                  header: "Label",
                  width: "auto",
                  cell: (record) => <span className="text-sm">{record.label ?? "—"}</span>,
                },
                {
                  key: "scopes",
                  header: "Scopes",
                  width: "120px",
                  cell: (record) => <span className="text-sm">{scopesOf(record)}</span>,
                },
                {
                  key: "created_at",
                  header: "Issued",
                  width: "200px",
                  cell: (record) => <span className="text-sm">{when(record.created_at)}</span>,
                },
                {
                  key: "revoked_at",
                  header: "State",
                  width: "120px",
                  cell: (record) => (
                    <Badge variant={isActive(record) ? "success" : "neutral"}>
                      {isActive(record) ? "active" : "revoked"}
                    </Badge>
                  ),
                },
                {
                  key: "revoke",
                  header: "",
                  width: "120px",
                  cell: (record) => (
                    <Button
                      variant="outline"
                      size="sm"
                      disabled={!isActive(record)}
                      onClick={() => setConfirming(record.credential_id)}
                    >
                      Revoke
                    </Button>
                  ),
                },
              ]}
            />
          </Card>
        )}
      </div>

      <ConfirmDialog
        open={confirming !== null}
        onOpenChange={(open) => {
          if (!open) setConfirming(null);
        }}
        title="Revoke this key?"
        description={
          confirming === null ? undefined : (
            <>
              Credential {shortId(confirming)}.
              {/* Behind the dialog the page is hidden from a screen reader, so
                  the one warning this action has is said here too. */}
              {needsWarning(state) ? <span className="mt-2 block">{SELF_REVOCATION_WARNING}</span> : null}
            </>
          )
        }
        confirmLabel="Revoke"
        onConfirm={() => (confirming === null ? Promise.resolve() : revoke(confirming))}
      />
    </div>
  );
}
