/**
 * Erasure, made deliberate.
 *
 * The target is typed back before the action is offered (FR-030), and the
 * comparison that decides it lives in the model where a test reaches it — it is
 * one `===` away from being decorative and it is the only thing between a
 * mis-click and a customer's data.
 *
 * One named target per confirmation. There is no multi-select and no "erase
 * all", and the model has no plural entry point for one to be built against
 * (FR-033).
 *
 * The counts shown are the deployment's own (FR-031); a target that was not
 * there is a success and says so (FR-032).
 */

import { useState } from "react";
import { Banner, Button, Card, HStack, Stack, Text, TextInput } from "@astryxdesign/core";

import type { Intent } from "../model/client.ts";
import {
  armed,
  confirmationPrompt,
  intendedFor,
  outcomeNotice,
  removedFrom,
  retyped,
  type ErasureIntent,
  type Target,
} from "../model/erasure.ts";
import type { Failure } from "../model/failure.ts";

type Request = (
  intent: Intent,
  body?: BodyInit,
) => Promise<{ ok: true; body: unknown } | { ok: false; failure: Failure }>;

interface Props {
  request: Request;
  onFailure: (failure: Failure) => void;
  /** The tenant a jump from Tenants opened this area for: prefilled, never confirmed for you. */
  presetTenant: string | null;
}

export function EraseArea({ request, onFailure, presetTenant }: Props) {
  const [kind, setKind] = useState<Target["kind"]>("tenant");
  // Prefilled only; Continue still asks for the identifier typed again.
  const [identifier, setIdentifier] = useState(presetTenant ?? "");
  // A document is erased within its tenant; the route names both.
  const [documentTenant, setDocumentTenant] = useState("");
  const [intent, setIntent] = useState<ErasureIntent | null>(null);
  const [outcome, setOutcome] = useState<string | null>(null);

  return (
    <Stack direction="vertical" gap={3} maxWidth={720}>
      <Banner
        status="warning"
        title="Erasure removes source bytes, everything derived from them, and the records that describe them"
        description="It cannot be undone, and the deployment reports what it removed."
      />

      {/* FR-045b: an erasure completing. `assertive`, and it is the one place
          that is: the operator just destroyed data and the counts are the only
          report of what went. */}
      <div role="status" aria-live="assertive">
        {outcome === null ? null : (
          <Banner status="info" title="Erasure complete" description={outcome} />
        )}
      </div>

      {intent === null ? (
        <Stack direction="vertical" gap={2}>
          <HStack gap={2}>
            <Button
              label="Tenant"
              variant={kind === "tenant" ? "primary" : "secondary"}
              onClick={() => setKind("tenant")}
            />
            <Button
              label="Document"
              variant={kind === "document" ? "primary" : "secondary"}
              onClick={() => setKind("document")}
            />
          </HStack>

          {kind === "document" ? (
            <TextInput label="Tenant" value={documentTenant} onChange={setDocumentTenant} />
          ) : null}

          <TextInput
            label={kind === "tenant" ? "Tenant identifier" : "Document identifier"}
            value={identifier}
            onChange={setIdentifier}
          />

          <HStack gap={2}>
          <Button
            label="Continue"
            variant="secondary"
            isDisabled={
              identifier.trim() === "" || (kind === "document" && documentTenant.trim() === "")
            }
            onClick={() => {
              setOutcome(null);
              setIntent(
                intendedFor(
                  kind === "tenant"
                    ? { kind, id: identifier.trim() }
                    : { kind, id: identifier.trim(), tenantId: documentTenant.trim() },
                ),
              );
            }}
          />
          </HStack>
        </Stack>
      ) : (
        <Card>
          <Stack direction="vertical" gap={2}>
          <Text size="sm">{confirmationPrompt(intent)}</Text>

          <TextInput
            label="Type the identifier again"
            {...{ autoComplete: "off" }}
            value={intent.typed}
            onChange={(typed) =>
              setIntent((current) => (current === null ? null : retyped(current, typed)))
            }
          />

          <HStack gap={2}>
            <Button
              label="Erase"
              variant="destructive"
              // Armed only on an exact match. No trimming, no case folding:
              // neither identifier has a form where "close enough" is a kindness.
              isDisabled={!armed(intent)}
              onClick={() => {
                const target = intent.target;
                void request(
                  target.kind === "tenant"
                    ? { type: "erase-tenant", tenantId: target.id }
                    : { type: "erase-document", tenantId: target.tenantId, blobId: target.id },
                ).then((answer) => {
                  if (!answer.ok) {
                    onFailure(answer.failure);
                    return;
                  }
                  setOutcome(outcomeNotice(removedFrom(answer.body)));
                  setIntent(null);
                  setIdentifier("");
                });
              }}
            />
            <Button label="Cancel" variant="secondary" onClick={() => setIntent(null)} />
          </HStack>
          </Stack>
        </Card>
      )}
    </Stack>
  );
}
