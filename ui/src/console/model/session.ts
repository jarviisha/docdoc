/**
 * The signed-in state, which the server now owns (ADR-0023 §1).
 *
 * **There is no credential here any more.** A login opens a session row in
 * Postgres and sets `docdoc_console`, `HttpOnly`, `SameSite=Strict`,
 * `Path=/v1/console` — the browser attaches it to every console request on
 * its own, and this file never reads or writes it (`check-no-persistence.mjs`
 * forbids `document.cookie` under `src/` regardless). What is left to track
 * client-side is only the username the server last confirmed, and whether a
 * confirmation has happened yet.
 *
 * The idle bound, the lockout, and the credential's own lifetime are the
 * server's (ADR-0023 §1, `DOCDOC_CONSOLE_IDLE_SECONDS`): this file computes
 * none of them. A session simply stops answering `who-am-i`, and the next
 * request that notices is told so by a `401 ConsoleSessionRequired` the
 * transport already distinguishes (`failure.ts`).
 */

import { describe, type Failure } from "./failure.ts";

export interface Session {
  /** The operator's name, or `null` before a login and after a sign-out. */
  username: string | null;
  /** Whether the first `GET /session` has answered yet, either way. */
  checked: boolean;
}

export function emptySession(): Session {
  return { username: null, checked: false };
}

export function signedIn(session: Session, username: string): Session {
  return { ...session, username, checked: true };
}

/**
 * Drop the operator's name.
 *
 * Used alike for an explicit sign-out and for any refusal that means the
 * session is gone (`unauthenticated` in `failure.ts`): both leave this console
 * signed out, and `checked` becomes `true` either way — an answer of "not
 * signed in" is still an answer.
 */
export function signedOut(session: Session): Session {
  return { ...session, username: null, checked: true };
}

/** Whether the console has a signed-in operator to act as. */
export function ready(session: Session): boolean {
  return session.username !== null;
}

/**
 * What a sign-out's answer leaves behind.
 *
 * Only a success, or a refusal meaning the session is already gone, signs the
 * console out. Any other failure leaves the cookie's session alive on the
 * server, so showing the sign-in form would claim a sign-out that did not
 * happen: the session stays and the failure is said instead.
 */
export function afterSignOut(
  session: Session,
  failure: Failure | null,
): { session: Session; notice: string | null } {
  if (failure === null || failure.kind === "unauthenticated") {
    return { session: signedOut(session), notice: null };
  }
  return { session, notice: describe(failure) };
}
