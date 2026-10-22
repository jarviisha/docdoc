/**
 * The console's frame: who is signed in, the sign-in form, and the areas.
 *
 * **It decides nothing.** Whether there is a session is the server's answer to
 * `who-am-i` (ADR-0023 §1), held by `console/model/session.ts`; what a failure
 * means is `console/model/failure.ts`; every request is a plan from
 * `console/model/client.ts`. The idle bound is the server's too — this page
 * keeps no clock, and learns a session has ended from the next `401`.
 */

import { Fragment, useCallback, useEffect, useState } from "react";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

import { send, sendForBlob } from "../../transport.ts";
import { requestFor, type Intent } from "../model/client.ts";
import { describe, failureOf, failureOfThrown, type Failure } from "../model/failure.ts";
import type { Area, Jump } from "../model/navigation.ts";
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
import { SubmitArea } from "./SubmitArea.tsx";
import { ParsersArea } from "./ParsersArea.tsx";
import { TenantsArea } from "./TenantsArea.tsx";
import { CallbacksArea } from "./CallbacksArea.tsx";

/** A sidebar group: real areas, and the ones still to come, which go nowhere yet. */
type NavEntry = { name: Area; label: string; danger?: boolean } | { soon: string };

const NAV: { heading: string | null; entries: NavEntry[] }[] = [
  { heading: null, entries: [{ name: "overview", label: "Overview" }] },
  {
    heading: "Operate",
    entries: [
      { name: "runs", label: "Runs" },
      { name: "submit", label: "Submit OCR" },
    ],
  },
  { heading: "Build", entries: [{ soon: "Schemas" }] },
  {
    heading: "Manage",
    entries: [
      { name: "tenants", label: "Tenants" },
      { name: "credentials", label: "Credentials" },
      { name: "callbacks", label: "Callbacks" },
    ],
  },
  {
    heading: "Settings",
    entries: [
      { name: "parsers", label: "Parsers" },
      { soon: "Providers" },
      { name: "erasure", label: "Erase…", danger: true },
    ],
  },
];

/** Each area's place in the sidebar, for the top bar's "Group / Area" trail. */
const PLACES = Object.fromEntries(
  NAV.flatMap((group) =>
    group.entries.flatMap((entry) =>
      "name" in entry ? [[entry.name, { group: group.heading, label: entry.label }]] : [],
    ),
  ),
) as Record<Area, { group: string | null; label: string }>;

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
  const [area, setArea] = useState<Area>("overview");
  const [openRunId, setOpenRunId] = useState<string | null>(null);
  // The tenant a jump from Tenants opened the area for; a sidebar visit has none.
  const [presetTenant, setPresetTenant] = useState<string | null>(null);
  // Bumped by every jump, so even a click on the area already open starts it afresh.
  const [visit, setVisit] = useState(0);

  const jump = useCallback(({ area: to, tenant }: Jump) => {
    setVisit((v) => v + 1);
    setOpenRunId(null);
    setPresetTenant(tenant);
    setArea(to);
  }, []);

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

  /** `request` for a page image: the same session handling, bytes instead of JSON. */
  const requestImage = useCallback(async (intent: Intent) => {
    const answer = await callForImage(intent);
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
      setPresetTenant(null);
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

  const navEntry = (entry: NavEntry) => {
    if (!("name" in entry)) {
      return (
        <span
          key={entry.soon}
          aria-disabled="true"
          className="flex items-center justify-between px-2 py-1 text-slate-400"
        >
          {entry.soon}
          <span className="text-[10px] tracking-wider uppercase">soon</span>
        </span>
      );
    }
    const selected = area === entry.name;
    return (
      <button
        key={entry.name}
        type="button"
        aria-current={selected ? "page" : undefined}
        onClick={() => jump({ area: entry.name, tenant: null })}
        className={cn(
          "rounded-sm px-2 py-1 text-left text-slate-700 outline-none hover:bg-slate-200/60 focus-visible:ring-2 focus-visible:ring-ring/50",
          selected && "bg-indigo-100 font-medium text-indigo-800 hover:bg-indigo-100",
          entry.danger === true && !selected && "text-red-600",
        )}
      >
        {entry.label}
      </button>
    );
  };

  return (
    <div className="flex h-screen">
      <aside className="flex w-52 shrink-0 flex-col overflow-y-auto border-r bg-slate-50 px-2 py-3">
        <p className="flex items-center gap-2 px-2 pb-2 font-semibold">
          <span aria-hidden="true" className="size-3.5 rounded-[1px] bg-primary" />
          docdoc console
        </p>
        <nav aria-label="Console" className="flex flex-col gap-0.5">
          {NAV.map((group) => (
            <Fragment key={group.heading ?? "top"}>
              {group.heading === null ? null : (
                <p className="px-2 pt-4 pb-1 text-[10px] font-medium tracking-widest text-slate-500 uppercase">
                  {group.heading}
                </p>
              )}
              {group.entries.map(navEntry)}
            </Fragment>
          ))}
        </nav>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="flex h-10 shrink-0 items-center justify-between border-b px-4">
          <span className="text-slate-500">
            {PLACES[area].group === null ? null : `${PLACES[area].group} / `}
            <span className="font-medium text-foreground">{PLACES[area].label}</span>
          </span>
          <div className="flex items-center gap-3">
            <span className="text-slate-500">Signed in as {session.username}</span>
            <Button variant="outline" size="sm" onClick={() => void signOut()}>
              Sign out
            </Button>
          </div>
        </header>

        <main className="flex flex-1 flex-col gap-3 overflow-auto p-4">
          {/* Empty, it would still take a gap: the negative margin gives it back. */}
          <div role="status" aria-live="polite" className="empty:-mb-3">
            {notice === null ? null : (
              <Alert variant="warning">
                <AlertTitle>{notice}</AlertTitle>
              </Alert>
            )}
          </div>
          <Fragment key={visit}>
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
              requestImage={requestImage}
              onFailure={handle}
              openRunId={openRunId}
              onOpenRun={setOpenRunId}
              presetTenant={presetTenant}
              onSubmit={() => jump({ area: "submit", tenant: null })}
            />
          ) : null}
          {area === "submit" ? (
            <SubmitArea
              request={request}
              onOpenRun={(runId) => {
                setOpenRunId(runId);
                setArea("runs");
              }}
            />
          ) : null}
          {area === "parsers" ? <ParsersArea request={request} /> : null}
          {area === "tenants" ? <TenantsArea request={request} onJump={jump} /> : null}
          {area === "credentials" ? (
            <CredentialArea request={request} onFailure={handle} presetTenant={presetTenant} />
          ) : null}
          {area === "callbacks" ? <CallbacksArea request={request} presetTenant={presetTenant} /> : null}
          {area === "erasure" ? (
            <EraseArea request={request} onFailure={handle} presetTenant={presetTenant} />
          ) : null}
          </Fragment>
        </main>
      </div>
    </div>
  );
}
