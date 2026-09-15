/**
 * The console's frame: the credential gate, the idle tick, and the areas.
 *
 * **It decides nothing.** Expiry is `session.expired`, the areas that exist are
 * `session.administrative`, and every request is a plan from
 * `console/model/client.ts`. What is here is the clock's source and the
 * arrangement of what the model already decided — which is the split
 * `check-model-boundary.mjs` enforces and the reason it exists: the rendering
 * layer carries no automated test, so a decision made here would ship with no
 * coverage.
 *
 * **Zero requests before a key is entered** (User Story 1 scenario 1). Nothing
 * probes on mount; the first request this component can make is the one the
 * operator's own action starts.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { Badge, Banner, Button, HStack, Stack, Text } from "@astryxdesign/core";

import { send, TransportError } from "../../transport.ts";
import { PROBE_TENANT, requestFor } from "../model/client.ts";
import { describe, failureOf, failureOfThrown, type Failure } from "../model/failure.ts";
import {
  asOpen,
  dropped,
  emptySession,
  expired,
  ready,
  touch,
  withAdministrative,
  withCredential,
  type Session,
} from "../model/session.ts";
import { CredentialPrompt } from "./CredentialPrompt.tsx";
import { RunArea } from "./RunArea.tsx";
import { CredentialArea } from "./CredentialArea.tsx";
import { EraseArea } from "./EraseArea.tsx";

/** How often the clock is read. The bound is fifteen minutes; this is not it. */
const TICK_MS = 15_000;

type Area = "runs" | "credentials" | "erasure";

export function Shell() {
  const [session, setSession] = useState<Session>(() => emptySession(Date.now()));
  const [notice, setNotice] = useState<string | null>(null);
  const [area, setArea] = useState<Area>("runs");

  // The clock, supplied to a decision made elsewhere (SC-003a).
  useEffect(() => {
    if (!ready(session)) return;
    const timer = setInterval(() => {
      setSession((current) => {
        if (!expired(current, Date.now())) return current;
        setNotice("The console was idle for fifteen minutes and discarded the key.");
        return dropped(current, Date.now());
      });
    }, TICK_MS);
    return () => clearInterval(timer);
  }, [session]);

  /**
   * One place where a refusal is acted on (FR-010).
   *
   * A credential the deployment will not accept is dropped immediately, so an
   * operator is not left clicking a dead page — and it is told apart from a
   * transport failure, which says nothing about the credential at all.
   */
  const handle = useCallback((failure: Failure) => {
    if (failure.kind === "unauthenticated") {
      setSession((current) => dropped(current, Date.now()));
    }
    setNotice(describe(failure));
  }, []);

  /** Every request the areas make goes through this, so the rules apply once. */
  const request = useMemo(
    () =>
      async (intent: Parameters<typeof requestFor>[0], body?: BodyInit) => {
        const plan = requestFor(intent);
        setSession((current) => touch(current, Date.now()));
        try {
          const response = await send(plan, {
            ...(body === undefined ? {} : { body }),
            ...(session.credential === null ? {} : { credential: session.credential }),
          });
          if (!response.ok) return { ok: false as const, failure: failureOf(response) };
          return { ok: true as const, body: response.body };
        } catch (error) {
          const failure =
            error instanceof TransportError
              ? failureOfThrown(error)
              : failureOfThrown(error);
          return { ok: false as const, failure };
        }
      },
    [session.credential],
  );

  /** The single probe that decides whether the administrative areas exist. */
  const discover = useCallback(
    async (next: Session) => {
      const plan = requestFor({ type: "list-credentials", tenant: PROBE_TENANT });
      try {
        const response = await send(plan, {
          ...(next.credential === null ? {} : { credential: next.credential }),
        });
        if (response.status === 401) {
          setSession(dropped(next, Date.now()));
          setNotice("That credential was not accepted. Enter another.");
          return;
        }
        // A 404 is ADR-0016 §6 answering "there is no such area", which is what
        // a principal without the administrative scope is told. It is hidden,
        // never disabled and never explained — explaining it would put back the
        // disclosure the 404 exists to remove (FR-028).
        setSession(withAdministrative(next, response.ok));
      } catch {
        setSession(next);
      }
    },
    [],
  );

  if (!ready(session)) {
    return (
      // `padding` because the viewer's App has it and this did not, which is why
      // the first screenshot of this console had every control glued to the
      // left edge of the window.
      <Stack direction="vertical" gap={4} padding={4} maxWidth={520}>
        <CredentialPrompt
          notice={notice}
          onCredential={(pasted) => {
            const next = withCredential(session, pasted, Date.now());
            setNotice(null);
            setSession(next);
            void discover(next);
          }}
          onContinueWithout={() => {
            const next = asOpen(session, Date.now());
            setNotice(null);
            setSession(next);
            void discover(next);
          }}
        />
      </Stack>
    );
  }

  const areas: Area[] = session.administrative ? ["runs", "credentials", "erasure"] : ["runs"];

  return (
    <Stack direction="vertical" gap={4} padding={4}>
      <HStack gap={2} align="center">
        <Text size="lg" weight="bold">
          docdoc operations console
        </Text>
        {/* FR-011: the mode is stated, because a key field that checks nothing
            and an authenticated deployment look identical from the outside. */}
        <Badge
          label={session.mode === "open" ? "authentication disabled" : "authenticated"}
          variant={session.mode === "open" ? "warning" : "neutral"}
        />
      </HStack>

      {/* FR-045b: a credential being refused, and an idle session ending, are
          both announced. Without this the operator learns the key was dropped by
          noticing that the page changed. */}
      <div role="status" aria-live="polite">
        {notice === null ? null : <Banner status="warning" title={notice} />}
      </div>

      {/* Plain buttons rather than a tab component: three of them, each a real
          control with a real accessible name, and nothing here needs the
          roving-focus behaviour a tab list brings with it. */}
      <HStack gap={2}>
        {areas.map((name) => (
          <Button
            key={name}
            label={name}
            variant={area === name ? "primary" : "secondary"}
            onClick={() => setArea(name)}
          />
        ))}
      </HStack>

      {area === "runs" ? <RunArea request={request} onFailure={handle} /> : null}
      {area === "credentials" && session.administrative ? (
        <CredentialArea request={request} onFailure={handle} />
      ) : null}
      {area === "erasure" && session.administrative ? (
        <EraseArea request={request} onFailure={handle} />
      ) : null}
    </Stack>
  );
}
