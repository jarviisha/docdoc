/**
 * Issue, list, revoke — through Milestone 10's routes and no others.
 *
 * The key appears once, with the sentence saying so, and is gone on
 * acknowledgement (FR-024). No row in the listing carries one (FR-025). The
 * warning before the first revocation is general, because this console does not
 * know which credential is its own and is not going to find out (FR-026a).
 */

import { useCallback, useEffect, useState } from "react";
import {
  Badge,
  Banner,
  Button,
  Card,
  Code,
  Field,
  HStack,
  Stack,
  Table,
  Text,
  pixel,
  proportional,
} from "@astryxdesign/core";

import type { Intent } from "../model/client.ts";
import {
  DISCLOSURE_WARNING,
  SELF_REVOCATION_WARNING,
  acknowledged,
  disclosureFrom,
  initialCredentials,
  isActive,
  needsWarning,
  scopesOf,
  toRecords,
  warned,
  withDisclosure,
  withRecords,
} from "../model/credentials.ts";
import type { CredentialRecord } from "../model/credentials.ts";
import type { Failure } from "../model/failure.ts";
import { shortId, when } from "../model/format.ts";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  onFailure: (failure: Failure) => void;
}

export function CredentialArea({ request, onFailure }: Props) {
  const [state, setState] = useState(initialCredentials);
  const [tenant, setTenant] = useState("");
  const [scope, setScope] = useState("");
  const [administrative, setAdministrative] = useState(false);

  // Which tenant's credentials are listed. The route requires it and the console
  // has no way to learn its own tenant — no route reports one, and adding one
  // would be the second new read SC-003 forbids. An operator issuing a key
  // already knows the tenant they are issuing it for.
  const [listing, setListing] = useState("");

  const refresh = useCallback(async () => {
    if (listing.trim() === "") return;
    const answer = await request({ type: "list-credentials", tenant: listing.trim() });
    if (answer.ok) setState((previous) => withRecords(previous, toRecords(answer.body)));
    else onFailure(answer.failure);
  }, [request, onFailure, listing]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <Stack direction="vertical" gap={3}>
      {state.disclosure === null ? null : (
        <Stack direction="vertical" gap={2} role="status" aria-live="assertive">
          {/* FR-045b: the key is shown once. A reader who does not see the
              screen has exactly one chance to be told that. */}
          <Banner status="warning" title={DISCLOSURE_WARNING} />
          <Field label="The issued key" inputID="issued-key">
            <input id="issued-key" type="text" readOnly value={state.disclosure.key} />
          </Field>
          <Button
            label="I have copied it"
            variant="primary"
            onClick={() => setState(acknowledged)}
          />
        </Stack>
      )}

      <Stack direction="vertical" gap={2}>
        <Text size="sm" weight="bold">
          Issue a credential
        </Text>
        <Field label="Tenant" inputID="issue-tenant">
          <input id="issue-tenant" type="text" value={tenant} onChange={(e) => setTenant(e.target.value)} />
        </Field>
        <Field label="Label (what it is for)" inputID="issue-label">
          <input id="issue-label" type="text" value={scope} onChange={(e) => setScope(e.target.value)} />
        </Field>
        <Field label="Grant the administrative scope" inputID="issue-admin">
          <input
            id="issue-admin"
            type="checkbox"
            checked={administrative}
            onChange={(event) => setAdministrative(event.target.checked)}
          />
        </Field>
        <Button
          label="Issue"
          variant="primary"
          isDisabled={tenant.trim() === ""}
          onClick={() => {
            void request(
              { type: "issue-credential", tenant: tenant.trim(), scope: scope.trim() },
              // The route takes `tenant_id`, an optional `label`, and `admin`
              // as a boolean — the administrative scope is the only one there
              // is, and `--admin` is how the command line grants it too.
              JSON.stringify({
                tenant_id: tenant.trim(),
                ...(scope.trim() === "" ? {} : { label: scope.trim() }),
                admin: administrative,
              }),
            ).then((answer) => {
              if (!answer.ok) return onFailure(answer.failure);
              setState((previous) => withDisclosure(previous, disclosureFrom(answer.body)));
              void refresh();
            });
          }}
        />
      </Stack>

      <Stack direction="vertical" gap={2}>
        <Text size="sm" weight="bold">
          Credentials
        </Text>

        <Field label="List credentials for tenant" inputID="list-tenant">
          <input
            id="list-tenant"
            type="text"
            value={listing}
            onChange={(event) => setListing(event.target.value)}
          />
        </Field>

        {needsWarning(state) ? <Banner status="info" title={SELF_REVOCATION_WARNING} /> : null}

        {state.records.length === 0 ? (
          <Text size="sm">
            {listing.trim() === ""
              ? "Name a tenant to list its credentials."
              : "This tenant holds no credentials."}
          </Text>
        ) : (
          <Card>
            <Table
              data={state.records as unknown as Record<string, unknown>[]}
              idKey="credential_id"
              hasHover
              density="compact"
              aria-label="Issued credentials. Keys are never shown here."
              columns={[
                {
                  key: "credential_id",
                  header: "Credential",
                  width: proportional(1),
                  renderCell: (row) => <Code>{shortId(row.credential_id as string)}</Code>,
                },
                {
                  key: "label",
                  header: "Label",
                  width: proportional(1),
                  renderCell: (row) => <Text size="sm">{(row.label as string) ?? "—"}</Text>,
                },
                {
                  key: "scopes",
                  header: "Scopes",
                  width: pixel(120),
                  renderCell: (row) => (
                    <Text size="sm">{scopesOf(row as unknown as CredentialRecord)}</Text>
                  ),
                },
                {
                  key: "created_at",
                  header: "Issued",
                  width: pixel(200),
                  renderCell: (row) => <Text size="sm">{when(row.created_at as string)}</Text>,
                },
                {
                  key: "revoked_at",
                  header: "State",
                  width: pixel(120),
                  renderCell: (row) => {
                    const record = row as unknown as CredentialRecord;
                    return (
                      <Badge
                        variant={isActive(record) ? "success" : "neutral"}
                        label={isActive(record) ? "active" : "revoked"}
                      />
                    );
                  },
                },
                {
                  key: "revoke",
                  header: "",
                  width: pixel(110),
                  resizable: false,
                  renderCell: (row) => {
                    const record = row as unknown as CredentialRecord;
                    return (
                      <Button
                        label="Revoke"
                        variant="destructive"
                        size="sm"
                        isDisabled={!isActive(record)}
                        onClick={() => {
                          // The warning is shown once per session, before the
                          // first revocation, and the action still proceeds: a
                          // leaked key is very often the one in your hand.
                          setState(warned);
                          void request({
                            type: "revoke-credential",
                            credentialId: record.credential_id,
                          }).then((answer) => {
                            if (!answer.ok) onFailure(answer.failure);
                            void refresh();
                          });
                        }}
                      />
                    );
                  },
                },
              ]}
            />
          </Card>
        )}
      </Stack>
    </Stack>
  );
}
