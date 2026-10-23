/**
 * The console's session gate: who is signed in, the sign-in form, and — once
 * signed in — the router, whose root route draws the frame (`../routes.tsx`).
 *
 * **It decides nothing.** Whether there is a session is the server's answer to
 * `who-am-i` (ADR-0023 §1), held by `console/model/session.ts`; what a failure
 * means is `console/model/failure.ts`; every request is a plan from
 * `console/model/client.ts`. The idle bound is the server's too — this page
 * keeps no clock, and learns a session has ended from the next `401`.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { RouterProvider } from "@tanstack/react-router";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";

import { send, sendForBlob } from "../../transport.ts";
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
import { FrameContext, router } from "../routes.tsx";
import { LoginForm } from "./LoginForm.tsx";
import { RequestProvider } from "./RequestContext.tsx";

const SESSION_ENDED = "Your session ended. Sign in again.";

type Answer = { ok: true; body: unknown } | { ok: false; failure: Failure };
type ImageAnswer = { ok: true; blob: Blob } | { ok: false; failure: Failure };

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

/** `call`'s counterpart for a page image; a refusal is classified from its JSON body like any other. */
async function callForImage(intent: Intent): Promise<ImageAnswer> {
  try {
    const response = await sendForBlob(requestFor(intent));
    if (!response.ok || response.blob === null) {
      return { ok: false, failure: failureOf({ ok: false, status: response.status, body: response.body }) };
    }
    return { ok: true, blob: response.blob };
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
  // Dropped on every way a session ends, so the next account to sign in never
  // sees the last one's lists, not even while its own load.
  const queryClient = useQueryClient();

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
      queryClient.clear();
      setSession(signedOut);
      setNotice(SESSION_ENDED);
    }
    return answer;
  }, [queryClient]);

  /** `request` for a page image: the same session handling, bytes instead of JSON. */
  const requestImage = useCallback(async (intent: Intent) => {
    const answer = await callForImage(intent);
    if (!answer.ok && answer.failure.kind === "unauthenticated") {
      queryClient.clear();
      setSession(signedOut);
      setNotice(SESSION_ENDED);
    }
    return answer;
  }, [queryClient]);

  const signIn = useCallback(async (username: string, password: string) => {
    const answer = await call({ type: "sign-in" }, JSON.stringify({ username, password }));
    if (answer.ok) {
      // The operator stays on the URL they were on, a run's own included.
      setNotice(null);
      setSession((s) => signedIn(s, usernameOf(answer.body)));
    } else {
      setNotice(describe(answer.failure));
    }
  }, []);

  const signOut = useCallback(async () => {
    const answer = await call({ type: "sign-out" });
    const after = afterSignOut(session, answer.ok ? null : answer.failure);
    if (!ready(after.session)) queryClient.clear();
    setNotice(after.notice);
    setSession(after.session);
  }, [session, queryClient]);

  const frame = useMemo(
    () => ({ username: session.username ?? "", notice, signOut: () => void signOut() }),
    [session.username, notice, signOut],
  );

  if (!ready(session)) {
    return (
      <div className="flex min-h-screen items-center justify-center p-4">
        <div className="flex w-full max-w-[400px] flex-col gap-4">
          <div role="status" aria-live="polite">
            {!session.checked ? <p className="text-sm">Loading…</p> : null}
            {unavailable === null ? null : (
              <Alert variant="destructive">
                <AlertTitle>The console is unavailable</AlertTitle>
                <AlertDescription>{unavailable}</AlertDescription>
              </Alert>
            )}
          </div>
          {session.checked && unavailable === null ? (
            <LoginForm notice={notice} onSubmit={(u, p) => void signIn(u, p)} />
          ) : null}
        </div>
      </div>
    );
  }

  return (
    <FrameContext value={frame}>
      <RequestProvider request={request} requestImage={requestImage}>
        <RouterProvider router={router} />
      </RequestProvider>
    </FrameContext>
  );
}
