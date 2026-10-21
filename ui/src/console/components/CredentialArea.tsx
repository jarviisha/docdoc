/**
 * Issue, list, revoke — through `/v1/console/credentials` (ADR-0023 §3).
 *
 * The key appears once, with the sentence saying so, and is gone on
 * acknowledgement (FR-024). No row in the listing carries one (FR-025). A
 * revocation is confirmed in its own row before it is sent, because it
 * cannot be undone and the button sits beside every active key.
 */

import { useCallback, useState } from "react";
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
  TextInput,
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
  issueBody,
  isActive,
  listedFor,
  listFailedFor,
  listingNote,
  needsWarning,
  scopesOf,
  showing,
  toRecords,
  warned,
  withDisclosure,
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
  const [label, setLabel] = useState("");

  // Which tenant's credentials to list: typed, then shown on Show — the route
  // requires one, and listing on every keystroke asks about tenants nobody meant.
  const [listing, setListing] = useState("");
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

  return (
    <Stack direction="vertical" gap={3}>
      {state.disclosure === null ? null : (
        <Stack direction="vertical" gap={2} role="status" aria-live="assertive">
          {/* FR-045b: the key is shown once. A reader who does not see the
              screen has exactly one chance to be told that. */}
          <Banner status="warning" title={DISCLOSURE_WARNING} />
          <TextInput label="The issued key" isReadOnly value={state.disclosure.key} />
          <HStack gap={2}>
            <Button
              label="I have copied it"
              variant="primary"
              onClick={() => setState(acknowledged)}
            />
          </HStack>
        </Stack>
      )}

      <Stack direction="vertical" gap={2} maxWidth={480}>
        <Text size="sm" weight="bold">
          Issue a credential
        </Text>
        <TextInput label="Tenant" value={tenant} onChange={setTenant} />
        <TextInput label="Label (what it is for)" value={label} onChange={setLabel} />
        <HStack gap={2}>
        <Button
          label="Issue"
          variant="primary"
          isDisabled={tenant.trim() === ""}
          onClick={() => {
            void request({ type: "issue-credential" }, issueBody(tenant.trim(), label)).then(
              (answer) => {
                if (!answer.ok) return onFailure(answer.failure);
                setState((previous) => withDisclosure(previous, disclosureFrom(answer.body)));
                void load(shown);
              },
            );
          }}
        />
        </HStack>
      </Stack>

      <Stack direction="vertical" gap={2}>
        <Text size="sm" weight="bold">
          Credentials
        </Text>

        <HStack gap={2} align="end">
          <TextInput
            label="List credentials for tenant"
            width={280}
            value={listing}
            onChange={setListing}
          />
          <Button
            label="Show"
            variant="secondary"
            isDisabled={listing.trim() === ""}
            onClick={() => {
              const which = listing.trim();
              setConfirming(null);
              setState((previous) => showing(previous, which));
              void load(which);
            }}
          />
        </HStack>

        {needsWarning(state) ? <Banner status="info" title={SELF_REVOCATION_WARNING} /> : null}

        {listingNote(state) !== null ? (
          <Text size="sm">{listingNote(state)}</Text>
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
                  width: pixel(300),
                  resizable: false,
                  renderCell: (row) => {
                    const record = row as unknown as CredentialRecord;
                    if (confirming !== record.credential_id) {
                      return (
                        <Button
                          label="Revoke"
                          variant="destructive"
                          size="sm"
                          isDisabled={!isActive(record)}
                          onClick={() => setConfirming(record.credential_id)}
                        />
                      );
                    }
                    return (
                      <HStack gap={2} align="center">
                        <Text size="sm">Revoke this key?</Text>
                        <Button
                          label="Confirm"
                          variant="destructive"
                          size="sm"
                          onClick={() => {
                            // The warning shows before the first revocation
                            // and the action still proceeds: a leaked key is
                            // very often the one somebody is still using.
                            setState(warned);
                            setConfirming(null);
                            void request({
                              type: "revoke-credential",
                              credentialId: record.credential_id,
                            }).then((answer) => {
                              if (!answer.ok) onFailure(answer.failure);
                              void load(shown);
                            });
                          }}
                        />
                        <Button
                          label="Cancel"
                          variant="secondary"
                          size="sm"
                          onClick={() => setConfirming(null)}
                        />
                      </HStack>
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
