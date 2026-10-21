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
import {
  Banner,
  Button,
  Card,
  Code,
  HStack,
  Selector,
  Stack,
  Table,
  Text,
  TextInput,
  pixel,
  proportional,
} from "@astryxdesign/core";

import {
  canRegister,
  canRevoke,
  registerBody,
  toCallbacks,
  type CallbackRecord,
} from "../model/callbacks.ts";
import type { Intent } from "../model/client.ts";
import { describe, type Failure } from "../model/failure.ts";
import { when } from "../model/format.ts";
import { toTenants } from "../model/submit.ts";

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
  const [tenant, setTenant] = useState(presetTenant ?? "");
  const [callbacks, setCallbacks] = useState<CallbackRecord[] | null>(null);
  // A 503: this deployment delivers no callbacks, said in place of the table.
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [problem, setProblem] = useState<string | null>(null);
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
      else setProblem(describe(answer.failure));
    });
    return () => {
      current = false;
    };
  }, [request]);

  const load = useCallback(
    async (which: string) => {
      const answer = await request({ type: "list-callbacks", tenantId: which });
      if (answer.ok) {
        setCallbacks(toCallbacks(answer.body));
        setUnavailable(null);
      } else if (answer.failure.kind === "unavailable") {
        setCallbacks(null);
        setUnavailable(describe(answer.failure));
      } else {
        setCallbacks(null);
        setProblem(describe(answer.failure));
      }
    },
    [request],
  );

  useEffect(() => {
    if (tenant !== "") void load(tenant);
  }, [tenant, load]);

  const register = () => {
    setProblem(null);
    setSending(true);
    void request({ type: "register-callback", tenantId: tenant }, registerBody(url, secret)).then(
      (answer) => {
        setSending(false);
        if (!answer.ok) return setProblem(describe(answer.failure));
        setSecret("");
        void load(tenant);
      },
    );
  };

  const revoke = (callbackId: string) => {
    setProblem(null);
    setSending(true);
    void request({ type: "revoke-callback", tenantId: tenant, callbackId }).then((answer) => {
      setSending(false);
      setConfirming(null);
      if (!answer.ok) setProblem(describe(answer.failure));
      void load(tenant);
    });
  };

  return (
    <Stack direction="vertical" gap={3}>
      <Text size="lg" weight="bold">
        Callbacks
      </Text>

      <Selector
        label="Tenant"
        placeholder="Choose a tenant"
        width={280}
        options={tenants}
        value={tenant}
        onChange={(chosen) => {
          setProblem(null);
          setConfirming(null);
          setCallbacks(null);
          setTenant(chosen);
        }}
      />

      <div role="status" aria-live="polite">
        {problem === null ? null : (
          <Banner status="error" title="The callbacks request failed" description={problem} />
        )}
        {unavailable === null ? null : (
          <Banner status="info" title="Callbacks are unavailable" description={unavailable} />
        )}
        {tenant !== "" && callbacks === null && unavailable === null && problem === null ? (
          <Text size="sm">Loading…</Text>
        ) : null}
        {callbacks !== null && callbacks.length === 0 ? (
          <Banner status="info" title="This tenant has no callbacks registered." />
        ) : null}
      </div>

      {callbacks !== null && callbacks.length > 0 ? (
        <Card>
          <Table
            data={callbacks as unknown as Record<string, unknown>[]}
            idKey="callback_id"
            hasHover
            density="compact"
            aria-label="Registered callbacks. Secrets are never shown here."
            columns={[
              {
                key: "url",
                header: "URL",
                width: proportional(1),
                renderCell: (row) => <Code>{row.url as string}</Code>,
              },
              {
                key: "created_at",
                header: "Created",
                width: pixel(200),
                renderCell: (row) => <Text size="sm">{when(row.created_at as string)}</Text>,
              },
              {
                key: "revoke",
                header: "",
                width: pixel(320),
                resizable: false,
                renderCell: (row) => {
                  const id = row.callback_id as string;
                  if (confirming !== id) {
                    return (
                      <Button
                        label="Revoke"
                        variant="destructive"
                        size="sm"
                        isDisabled={!canRevoke(sending)}
                        onClick={() => setConfirming(id)}
                      />
                    );
                  }
                  return (
                    <HStack gap={2} align="center">
                      <Text size="sm">Revoke this callback?</Text>
                      <Button
                        label="Confirm"
                        variant="destructive"
                        size="sm"
                        isDisabled={!canRevoke(sending)}
                        onClick={() => revoke(id)}
                      />
                      <Button
                        label="Keep"
                        variant="secondary"
                        size="sm"
                        isDisabled={!canRevoke(sending)}
                        onClick={() => setConfirming(null)}
                      />
                    </HStack>
                  );
                },
              },
            ]}
          />
        </Card>
      ) : null}

      {/* Offered once the list answered: a deployment that cannot list callbacks
          (503, or no such route) cannot register one either. */}
      {callbacks !== null ? (
        <Card maxWidth={520} padding={4}>
          <Stack direction="vertical" gap={3}>
            <Text size="sm" weight="bold">
              Register a callback
            </Text>
            <TextInput label="URL" value={url} onChange={setUrl} />
            {/* `autoComplete` is not in TextInput's typed props; its rest props
                land on the `<input>`, as LoginForm does. */}
            <TextInput
              label="Secret"
              type="password"
              {...{ autoComplete: "off" }}
              value={secret}
              onChange={setSecret}
            />
            <HStack gap={2}>
              <Button
                label="Register"
                variant="primary"
                isDisabled={!canRegister(url, secret, sending)}
                isLoading={sending}
                onClick={register}
              />
            </HStack>
          </Stack>
        </Card>
      ) : null}
    </Stack>
  );
}
