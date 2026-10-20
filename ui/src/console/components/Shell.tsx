/**
 * The console's frame: who is signed in, the sign-in form, and the areas.
 *
 * **It decides nothing.** Whether there is a session is the server's answer to
 * `who-am-i` (ADR-0023 §1), held by `console/model/session.ts`; what a failure
 * means is `console/model/failure.ts`; every request is a plan from
 * `console/model/client.ts`. The idle bound is the server's too — this page
 * keeps no clock, and learns a session has ended from the next `401`.
 */

import { useCallback, useEffect, useState } from "react";
import { Banner, Button, Stack, Text } from "@astryxdesign/core";

import { send } from "../../transport.ts";
import { requestFor, type Intent } from "../model/client.ts";
import { describe, failureOf, failureOfThrown, type Failure } from "../model/failure.ts";
import {
  afterSignOut,
  emptySession,
  ready,
  signedIn,
  signedOut,
  type Session,
} from "../model/session.ts";
import { LoginForm } from "./LoginForm.tsx";
import { RunArea } from "./RunArea.tsx";
import { CredentialArea } from "./CredentialArea.tsx";
import { EraseArea } from "./EraseArea.tsx";
import { OverviewArea } from "./OverviewArea.tsx";

type Area = "overview" | "runs" | "credentials" | "erasure";

const MONITORING: { name: Area; label: string }[] = [
  { name: "overview", label: "Overview" },
  { name: "runs", label: "Runs" },
];

const ADMIN: { name: Area; label: string }[] = [
  { name: "credentials", label: "Credentials" },
  { name: "erasure", label: "Erasure" },
];

const SESSION_ENDED = "Your session ended. Sign in again.";

type Answer = { ok: true; body: unknown } | { ok: false; failure: Failure };

/** One request, classified. No side effect on the session. */
async function call(intent: Intent, body?: BodyInit): Promise<Answer> {
  try {
    const response = await send(requestFor(intent), body === undefined ? {} : { body });
    if (!response.ok) return { ok: false, failure: failureOf(response) };
    return { ok: true, body: response.body };
  } catch (error) {
    return { ok: false, failure: failureOfThrown(error) };
  }
}

/** A field read out of an answer body, for `{username}`. */
function usernameOf(body: unknown): string {
  const name = (body as { username?: unknown } | null)?.username;
  return typeof name === "string" ? name : "";
}

export function Shell() {
  const [session, setSession] = useState<Session>(emptySession);
  const [notice, setNotice] = useState<string | null>(null);
  const [unavailable, setUnavailable] = useState<string | null>(null);
  const [area, setArea] = useState<Area>("overview");
  const [openRunId, setOpenRunId] = useState<string | null>(null);

  // Ask once whether a session already exists: a reload keeps the cookie.
  useEffect(() => {
    let current = true;
    void call({ type: "who-am-i" }).then((answer) => {
      if (!current) return;
      if (answer.ok) {
        setSession((s) => signedIn(s, usernameOf(answer.body)));
        return;
      }
      setSession(signedOut);
      if (answer.failure.kind === "unavailable") setUnavailable(describe(answer.failure));
      else if (answer.failure.kind !== "unauthenticated") setNotice(describe(answer.failure));
    });
    return () => {
      current = false;
    };
  }, []);

  /**
   * Every request the areas make goes through this, so a session ending is
   * acted on in one place whichever area noticed it (FR-010).
   */
  const request = useCallback(async (intent: Intent, body?: BodyInit) => {
    const answer = await call(intent, body);
    if (!answer.ok && answer.failure.kind === "unauthenticated") {
      setSession(signedOut);
      setNotice(SESSION_ENDED);
    }
    return answer;
  }, []);

  /** What an area reports. A session ending was already handled above. */
  const handle = useCallback((failure: Failure) => {
    if (failure.kind !== "unauthenticated") setNotice(describe(failure));
  }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    const answer = await call({ type: "sign-in" }, JSON.stringify({ username, password }));
    if (answer.ok) {
      setNotice(null);
      setArea("overview");
      setOpenRunId(null);
      setSession((s) => signedIn(s, usernameOf(answer.body)));
    } else {
      setNotice(describe(answer.failure));
    }
  }, []);

  const signOut = useCallback(async () => {
    const answer = await call({ type: "sign-out" });
    const after = afterSignOut(session, answer.ok ? null : answer.failure);
    setNotice(after.notice);
    setSession(after.session);
  }, [session]);

  if (!ready(session)) {
    return (
      // `padding` because without it every control sits on the window's edge.
      <Stack direction="vertical" gap={4} padding={4} maxWidth={520}>
        <div role="status" aria-live="polite">
          {!session.checked ? <Text size="sm">Loading…</Text> : null}
          {unavailable === null ? null : (
            <Banner status="error" title="The console is unavailable" description={unavailable} />
          )}
        </div>
        {session.checked && unavailable === null ? (
          <LoginForm notice={notice} onSubmit={(u, p) => void signIn(u, p)} />
        ) : null}
      </Stack>
    );
  }

  const navButton = ({ name, label }: { name: Area; label: string }) => (
    <Button
      key={name}
      label={label}
      variant={area === name ? "primary" : "ghost"}
      aria-current={area === name ? "page" : undefined}
      onClick={() => {
        setArea(name);
        setOpenRunId(null);
      }}
    />
  );

  return (
    <div style={{ display: "flex", minHeight: "100vh" }}>
      {/* Plain buttons rather than a tab component: each a real control with a
          real accessible name, and nothing here needs the roving-focus
          behaviour a tab list brings with it. */}
      <nav aria-label="Console" style={{ width: 200, flexShrink: 0, display: "flex" }}>
        <Stack direction="vertical" gap={2} padding={4} justify="between" width="100%">
          <Stack direction="vertical" gap={2}>
            <Text weight="bold">docdoc console</Text>
            {MONITORING.map(navButton)}
            <Text size="sm" color="secondary">
              Admin
            </Text>
            {ADMIN.map(navButton)}
          </Stack>
          <Stack direction="vertical" gap={2}>
            <Text size="sm">Signed in as {session.username}</Text>
            <Button label="Sign out" variant="secondary" size="sm" onClick={() => void signOut()} />
          </Stack>
        </Stack>
      </nav>

      <main style={{ flex: 1, minWidth: 0 }}>
        <Stack direction="vertical" gap={4} padding={4}>
          <div role="status" aria-live="polite">
            {notice === null ? null : <Banner status="warning" title={notice} />}
          </div>

          {area === "overview" ? (
            <OverviewArea
              request={request}
              onOpenRun={(runId) => {
                setOpenRunId(runId);
                setArea("runs");
              }}
            />
          ) : null}
          {area === "runs" ? (
            <RunArea
              request={request}
              onFailure={handle}
              openRunId={openRunId}
              onOpenRun={setOpenRunId}
            />
          ) : null}
          {area === "credentials" ? <CredentialArea request={request} onFailure={handle} /> : null}
          {area === "erasure" ? <EraseArea request={request} onFailure={handle} /> : null}
        </Stack>
      </main>
    </div>
  );
}
