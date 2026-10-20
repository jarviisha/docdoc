/**
 * What went wrong, in the kinds this console has to tell apart.
 *
 * The console invents no error vocabulary of its own (FR-039): what the
 * deployment said is passed through, and what is added here is only *which
 * kind happened*. ADR-0023's session adds two kinds a pasted key never
 * produced:
 *
 *   - `unavailable` — `ConsoleUnavailable`, 503. There is no run-state
 *     database to hold a session at all, which is nothing like a wrong
 *     password and must not read as one.
 *   - `refused` — `LoginRefused`, 401. A login itself was turned down: wrong
 *     username, wrong password, locked, or disabled all share this one body
 *     (ADR-0023 §1), and unlike `unauthenticated` it does not mean the session
 *     is gone — there never was one to lose.
 *
 * `unauthenticated` keeps covering `ConsoleSessionRequired` (401, no session
 * or an expired one) and `ConsoleHeaderMissing` (403): both mean "the console
 * is not signed in as far as this request is concerned," and the session
 * drops its username and asks again.
 */

import { TransportError, type Response } from "../../transport.ts";

export type Failure =
  /** The deployment answered, and said no. Its own typed error is carried. */
  | { kind: "reported"; status: number; message: string }
  /** No session, or one the gate would not accept. The session signs out. */
  | { kind: "unauthenticated"; message: string }
  /** The run-state database the console needs is absent (`ConsoleUnavailable`). */
  | { kind: "unavailable"; message: string }
  /** A login was turned down (`LoginRefused`) — not a session problem at all. */
  | { kind: "refused"; message: string }
  /** No usable answer arrived. Nothing is known about the deployment's view. */
  | { kind: "transport"; message: string }
  /**
   * The run was here and is gone (`specs/010` FR-011).
   *
   * **T075, appended by convergence.** A `410` used to arrive as `reported` and
   * the detail view said only "this run was removed", while the comment above
   * that banner cited FR-023 — *"the removal time and the policy under which it
   * was removed"* — and showed neither. The body carries both fields; nothing
   * read them.
   *
   * Carried as its own kind rather than parsed in the component, for the reason
   * every decision here lives in the model: the rendering layer has no automated
   * test, so a body read there is a requirement with no coverage.
   */
  | { kind: "removed"; deletedAt: string; policy: string }
  /**
   * The route answered as though it does not exist.
   *
   * Kept apart from `reported` because ADR-0016 §6 makes an administrative route
   * answer `404` to a principal without the scope — so for this console a `404`
   * on an administrative area means *this area is absent*, and the correct
   * response is to hide it rather than to report an error (FR-028).
   */
  | { kind: "absent" };

/** The message a deployment's error body carries, without inventing one. */
function messageOf(body: unknown): string {
  if (typeof body === "object" && body !== null) {
    const wrapper = (body as { error?: unknown }).error;
    if (typeof wrapper === "object" && wrapper !== null) {
      const message = (wrapper as { message?: unknown }).message;
      if (typeof message === "string") return message;
      const cls = (wrapper as { class?: unknown }).class;
      if (typeof cls === "string") return cls;
    }
    if (typeof wrapper === "string") return wrapper;
    const detail = (body as { detail?: unknown }).detail;
    if (typeof detail === "string") return detail;
  }
  return "the deployment reported a failure and gave no message";
}

/** The console's typed error class, as `{"error":{"class": ...}}` carries it. */
function classOf(body: unknown): string | null {
  if (typeof body !== "object" || body === null) return null;
  const wrapper = (body as { error?: unknown }).error;
  if (typeof wrapper !== "object" || wrapper === null) return null;
  const cls = (wrapper as { class?: unknown }).class;
  return typeof cls === "string" ? cls : null;
}

/** The tombstone's two fields, or `null` when the body is not one. */
function tombstoneIn(body: unknown): { deletedAt: string; policy: string } | null {
  if (typeof body !== "object" || body === null) return null;
  const record = body as { deleted_at?: unknown; policy?: unknown };
  if (typeof record.deleted_at !== "string" || typeof record.policy !== "string") return null;
  return { deletedAt: record.deleted_at, policy: record.policy };
}

/** Classify an answered request. Callers pass only responses that are not ok. */
export function failureOf(response: Response): Failure {
  if (response.status === 410) {
    const stone = tombstoneIn(response.body);
    // A `410` whose body is not a tombstone is still a removal, and saying so
    // with empty fields beats claiming the route answered something else.
    return stone === null
      ? { kind: "removed", deletedAt: "", policy: "" }
      : { kind: "removed", ...stone };
  }
  if (response.status === 503) {
    return { kind: "unavailable", message: messageOf(response.body) };
  }
  if (response.status === 401 && classOf(response.body) === "LoginRefused") {
    return { kind: "refused", message: messageOf(response.body) };
  }
  if (response.status === 401 || response.status === 403) {
    return {
      kind: "unauthenticated",
      message: messageOf(response.body),
    };
  }
  if (response.status === 404) return { kind: "absent" };
  return { kind: "reported", status: response.status, message: messageOf(response.body) };
}

/** Classify a thrown error. Only `TransportError` is expected here. */
export function failureOfThrown(error: unknown): Failure {
  if (error instanceof TransportError) return { kind: "transport", message: error.message };
  return {
    kind: "transport",
    message: error instanceof Error ? error.message : "the request did not complete",
  };
}

/** What a human is told. One sentence, and never a credential. */
export function describe(failure: Failure): string {
  switch (failure.kind) {
    case "unauthenticated":
    case "unavailable":
    case "refused":
    case "transport":
    case "reported":
      return failure.message;
    case "removed":
      // FR-023: when, under which policy, and nothing else about what it held.
      // A tombstone carries four fields precisely so that being told about one
      // discloses nothing about its contents.
      return failure.deletedAt === ""
        ? "this run was removed."
        : `this run was removed on ${failure.deletedAt} under the ${failure.policy} policy.`;
    case "absent":
      return "this deployment does not offer that.";
  }
}
