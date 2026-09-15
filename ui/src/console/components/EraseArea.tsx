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
import { Banner, Button, Card, Field, HStack, Stack, Text } from "@astryxdesign/core";

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
}

export function EraseArea({ request, onFailure }: Props) {
  const [kind, setKind] = useState<Target["kind"]>("tenant");
  const [identifier, setIdentifier] = useState("");
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

          <Field
            label={kind === "tenant" ? "Tenant identifier" : "Document identifier"}
            inputID="erase-target"
          >
            <input
              id="erase-target"
              type="text"
              value={identifier}
              onChange={(event) => setIdentifier(event.target.value)}
            />
          </Field>

          <Button
            label="Continue"
            variant="secondary"
            isDisabled={identifier.trim() === ""}
            onClick={() => {
              setOutcome(null);
              setIntent(intendedFor({ kind, id: identifier.trim() }));
            }}
          />
        </Stack>
      ) : (
        <Card>
          <Stack direction="vertical" gap={2}>
          <Text size="sm">{confirmationPrompt(intent)}</Text>

          <Field label="Type the identifier again" inputID="erase-confirm">
            <input
              id="erase-confirm"
              type="text"
              autoComplete="off"
              value={intent.typed}
              onChange={(event) =>
                setIntent((current) => (current === null ? null : retyped(current, event.target.value)))
              }
            />
          </Field>

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
                    : { type: "erase-document", blobId: target.id },
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
