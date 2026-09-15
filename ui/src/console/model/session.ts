/**
 * The credential, the mode, and the idle bound — the whole of "signed in".
 *
 * **There is no session.** No identifier, no server-side record, no cookie, no
 * token exchange, no expiry policy anybody administers (FR-009). What exists is
 * a key an operator pasted, held in this page's memory for as long as the page
 * lives. A reload loses it, and that is designed rather than tolerated: the
 * alternative was a login route, a session table, CSRF, and an expiry policy —
 * four new security surfaces written by this project to guard a credential the
 * deployment already has and already rotates (ADR-0019 §4).
 *
 * Nothing here touches `localStorage`, `sessionStorage`, IndexedDB, or the Cache
 * API, and `check-readonly.mjs` fails the build if anything under `src/` does
 * (FR-007, SC-004).
 *
 * Every decision lives in this file rather than in a component, because the
 * rendering layer carries no automated test by decision — so a decision that
 * drifts into a component is a requirement that silently loses its coverage.
 */

/**
 * How long an untouched console keeps the key (FR-009a, research R4).
 *
 * Fifteen minutes. Short enough to matter for a tab left open on an unlocked
 * machine, long enough that reading a run list does not cost the key mid-task.
 *
 * This is the one place in this milestone where the larger mechanism was chosen:
 * the console erases tenants, and the identifier the erasure confirmation asks
 * an operator to re-type is on screen for anyone to copy. "The operator locks
 * their laptop" is not a control this project can assert.
 */
export const IDLE_BOUND_MS = 15 * 60 * 1000;

/** Which authentication posture the deployment turned out to have. */
export type Mode = "unknown" | "authenticated" | "open";

export interface Session {
  /** The pasted key, or `null` before one is entered and after it is dropped. */
  credential: string | null;
  mode: Mode;
  /** Whether the credential carries the administrative scope (research R7). */
  administrative: boolean;
  /** The last moment the operator did something. The only input to expiry. */
  lastActivityAt: number;
}

export function emptySession(now: number): Session {
  return { credential: null, mode: "unknown", administrative: false, lastActivityAt: now };
}

/**
 * A pasted key, cleaned of what a clipboard adds.
 *
 * A trailing newline is the ordinary case rather than the strange one — it comes
 * from copying a line out of a terminal — and sending it produces a refusal that
 * looks exactly like a wrong key (Edge Cases).
 */
export function normalise(pasted: string): string {
  return pasted.trim();
}

export function withCredential(session: Session, pasted: string, now: number): Session {
  return {
    ...session,
    credential: normalise(pasted) === "" ? null : normalise(pasted),
    mode: "authenticated",
    lastActivityAt: now,
  };
}

/**
 * The deployment accepted a request with no credential (FR-011, research R6).
 *
 * Discovered by trying, not by asking. A route reporting whether authentication
 * is enabled would be a second new read — which costs SC-003 — and an
 * unauthenticated oracle describing the deployment's security posture.
 */
export function asOpen(session: Session, now: number): Session {
  return { ...session, credential: null, mode: "open", lastActivityAt: now };
}

/** What the probe of the credential listing found (FR-028). */
export function withAdministrative(session: Session, administrative: boolean): Session {
  return { ...session, administrative };
}

export function touch(session: Session, now: number): Session {
  return { ...session, lastActivityAt: now };
}

/**
 * Whether the idle bound has passed.
 *
 * A pure function of the session and the clock, so a test can reach it without
 * waiting fifteen minutes and without a fake timer. The component ticks; this
 * decides (SC-003a).
 *
 * An **open** deployment has no credential to discard, so it never expires —
 * expiring it would drop an operator back to a prompt that checks nothing.
 */
export function expired(session: Session, now: number): boolean {
  if (session.mode !== "authenticated" || session.credential === null) return false;
  return now - session.lastActivityAt >= IDLE_BOUND_MS;
}

/**
 * Drop the credential and everything learned with it.
 *
 * Used by expiry and by a refusal alike, and it is one function on purpose: a
 * console that forgot the key but kept `administrative: true` would show an
 * area the next credential may not be allowed to see.
 */
export function dropped(session: Session, now: number): Session {
  return { credential: null, mode: session.mode, administrative: false, lastActivityAt: now };
}

/** Whether the console may issue requests at all. */
export function ready(session: Session): boolean {
  return session.mode === "open" || session.credential !== null;
}
