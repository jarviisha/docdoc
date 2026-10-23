/**
 * Issue, list, revoke — through `/v1/console/credentials` (ADR-0023 §3).
 *
 * The key appears once, with the sentence saying so, and is gone on
 * acknowledgement (FR-024). No row in the listing carries one (FR-025). A
 * revocation is confirmed in its own row before it is sent, because it
 * cannot be undone and the button sits beside every active key.
 */

import { useEffect, useState } from "react";
import { getRouteApi } from "@tanstack/react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";

import {
  DISCLOSURE_WARNING,
  SELF_REVOCATION_WARNING,
  acknowledged,
  disclosureFrom,
  initialCredentials,
  issueBody,
  isActive,
  listingNote,
  needsWarning,
  scopesOf,
  toRecords,
  warned,
  withDisclosure,
} from "../model/credentials.ts";
import { describe } from "../model/failure.ts";
import { shortId } from "../model/format.ts";
import { tenantSearch } from "../model/search.ts";
import { ConfirmDialog } from "./ConfirmDialog.tsx";
import { CopyableId } from "./CopyableId.tsx";
import { DataTable } from "./DataTable.tsx";
import { TextField } from "./fields.tsx";
import { SectionHeading } from "./SectionHeading.tsx";
import { Time } from "./Time.tsx";
import { useAct, useIntent } from "./useIntent.ts";

const credentialsApi = getRouteApi("/credentials");

/** The tenant in the URL is the one listed; Show pushes it, so Back returns to the last one. */
export function CredentialArea() {
  const shown = credentialsApi.useSearch().tenant ?? "";
  const navigate = credentialsApi.useNavigate();
  const act = useAct();
  const [state, setState] = useState(initialCredentials);
  // The issue form starts from the listed tenant and is its own afterwards.
  const [tenant, setTenant] = useState(shown);
  const [label, setLabel] = useState("");
  // What is typed in the Show field, apart from what is listed; it follows the
  // URL when Back or a link changes it.
  const [listing, setListing] = useState(shown);
  useEffect(() => setListing(shown), [shown]);
  // The row awaiting "Revoke this key?", by credential id.
  const [confirming, setConfirming] = useState<string | null>(null);
  // An issue or a revoke that failed; a listing failure is the query's.
  const [writeProblem, setWriteProblem] = useState<string | null>(null);

  const list = useIntent(shown === "" ? null : { type: "list-credentials", tenant: shown }, {
    select: toRecords,
  });
  const records = list.data ?? [];
  const problem = writeProblem ?? (list.isError ? describe(list.error) : null);
  const note = listingNote(shown, list.isError ? "failed" : list.isPending ? "pending" : "loaded", records);

  const show = () => {
    if (listing.trim() === "") return;
    setConfirming(null);
    void navigate({ search: (previous) => tenantSearch({ ...previous, tenant: listing }) });
  };

  const revoke = async (credentialId: string) => {
    // The warning shows before the first revocation and the action still
    // proceeds: a leaked key is very often the one somebody is still using.
    setState(warned);
    setWriteProblem(null);
    const answer = await act({ type: "revoke-credential", credentialId });
    setConfirming(null);
    if (!answer.ok) setWriteProblem(describe(answer.failure));
  };

  const issue = async () => {
    setWriteProblem(null);
    const answer = await act({ type: "issue-credential" }, issueBody(tenant.trim(), label));
    if (!answer.ok) return setWriteProblem(describe(answer.failure));
    setState((previous) => withDisclosure(previous, disclosureFrom(answer.body)));
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
        <SectionHeading>Issue a credential</SectionHeading>
        <TextField label="Tenant" value={tenant} onChange={setTenant} />
        <TextField label="Label (what it is for)" value={label} onChange={setLabel} />
        <div className="flex gap-2">
          <Button
            disabled={tenant.trim() === ""}
            onClick={() => void issue()}
          >
            Issue
          </Button>
        </div>
      </div>

      <div className="flex flex-col gap-2">
        <SectionHeading>Credentials</SectionHeading>

        <div className="flex items-end gap-2">
          <TextField
            label="List credentials for tenant"
            className="w-[280px]"
            value={listing}
            onChange={setListing}
            onEnter={show}
          />
          <Button variant="outline" disabled={listing.trim() === ""} onClick={show}>
            Show
          </Button>
        </div>

        {needsWarning(state) ? (
          <Alert variant="info">
            <AlertTitle>{SELF_REVOCATION_WARNING}</AlertTitle>
          </Alert>
        ) : null}

        <div role="status" aria-live="polite" className="empty:-mb-2">
          {problem === null ? null : (
            <Alert variant="destructive">
              <AlertTitle>The credentials request failed</AlertTitle>
              <AlertDescription>{problem}</AlertDescription>
            </Alert>
          )}
        </div>

        {note !== null ? (
          <p className="text-sm">{note}</p>
        ) : (
          <Card className="py-0">
            <DataTable
              rows={records}
              rowKey={(record) => record.credential_id}
              label="Issued credentials. Keys are never shown here."
              columns={[
                {
                  key: "credential_id",
                  header: "Credential",
                  width: "auto",
                  cell: (record) => <CopyableId value={record.credential_id} display={shortId(record.credential_id)} />,
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
                  cell: (record) => <Time iso={record.created_at} />,
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
