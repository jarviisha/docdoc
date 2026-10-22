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
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";

import type { Intent } from "../model/client.ts";
import {
  armed,
  confirmationPrompt,
  intendedFor,
  outcomeNotice,
  removedFrom,
  retyped,
  switchedToDocument,
  type ErasureIntent,
  type Target,
} from "../model/erasure.ts";
import type { Failure } from "../model/failure.ts";
import { ConfirmDialog } from "./ConfirmDialog.tsx";
import { TextField } from "./fields.tsx";

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

  const erase = async (current: ErasureIntent) => {
    const target = current.target;
    const answer = await request(
      target.kind === "tenant"
        ? { type: "erase-tenant", tenantId: target.id }
        : { type: "erase-document", tenantId: target.tenantId, blobId: target.id },
    );
    // Closed either way, so a refusal is read on the page, not behind the dialog.
    setIntent(null);
    if (!answer.ok) {
      onFailure(answer.failure);
      return;
    }
    setOutcome(outcomeNotice(removedFrom(answer.body)));
    setIdentifier("");
  };

  return (
    <div className="flex max-w-[720px] flex-col gap-3">
      <Alert variant="warning">
        <AlertTitle>
          Erasure removes source bytes, everything derived from them, and the records that describe them
        </AlertTitle>
        <AlertDescription>It cannot be undone, and the deployment reports what it removed.</AlertDescription>
      </Alert>

      {/* FR-045b: an erasure completing. `assertive`, and it is the one place
          that is: the operator just destroyed data and the counts are the only
          report of what went. */}
      <div role="status" aria-live="assertive">
        {outcome === null ? null : (
          <Alert variant="info">
            <AlertTitle>Erasure complete</AlertTitle>
            <AlertDescription>{outcome}</AlertDescription>
          </Alert>
        )}
      </div>

      <div className="flex flex-col gap-2">
        <div className="flex gap-2">
          <Button variant={kind === "tenant" ? "default" : "outline"} onClick={() => setKind("tenant")}>
            Tenant
          </Button>
          <Button
            variant={kind === "document" ? "default" : "outline"}
            onClick={() => {
              if (kind === "tenant") {
                const next = switchedToDocument(presetTenant, {
                  identifier,
                  documentTenant,
                });
                setIdentifier(next.identifier);
                setDocumentTenant(next.documentTenant);
              }
              setKind("document");
            }}
          >
            Document
          </Button>
        </div>

        {kind === "document" ? (
          <TextField label="Tenant" value={documentTenant} onChange={setDocumentTenant} />
        ) : null}

        <TextField
          label={kind === "tenant" ? "Tenant identifier" : "Document identifier"}
          value={identifier}
          onChange={setIdentifier}
        />

        <div className="flex gap-2">
          <Button
            variant="outline"
            disabled={identifier.trim() === "" || (kind === "document" && documentTenant.trim() === "")}
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
          >
            Continue
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={intent !== null}
        onOpenChange={(open) => {
          if (!open) setIntent(null);
        }}
        title="Erase permanently?"
        description={intent === null ? undefined : confirmationPrompt(intent)}
        confirmLabel="Erase"
        // Armed only on an exact match. No trimming, no case folding:
        // neither identifier has a form where "close enough" is a kindness.
        confirmDisabled={intent === null || !armed(intent)}
        onConfirm={() => (intent === null ? Promise.resolve() : erase(intent))}
      >
        {intent === null ? null : (
          <TextField
            label="Type the identifier again"
            autoComplete="off"
            value={intent.typed}
            onChange={(typed) => setIntent((current) => (current === null ? null : retyped(current, typed)))}
          />
        )}
      </ConfirmDialog>
    </div>
  );
}
