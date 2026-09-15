/**
 * What went wrong, in the three kinds this console has to tell apart.
 *
 * The viewer already separates two of them and the spec's Edge Cases are
 * explicit that they must not be merged: *the deployment reported a failure* is
 * not *this browser never heard a usable answer*. The console needs a third,
 * because it holds a credential: **the credential was not accepted** is neither
 * of the others, and reporting it as a transport failure leaves an operator
 * retrying a key that will never work (FR-010, FR-039).
 *
 * The console invents no error vocabulary of its own (FR-039). What the
 * deployment said is passed through; what is added here is only *which of the
 * three happened*.
 */

import { TransportError, type Response } from "../../transport.ts";

export type Failure =
  /** The deployment answered, and said no. Its own typed error is carried. */
  | { kind: "reported"; status: number; message: string }
  /** The credential was refused. The session drops it and asks again. */
  | { kind: "unauthenticated"; message: string }
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
      return "that credential was not accepted. Enter another.";
    case "transport":
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
    case "reported":
      return failure.message;
  }
}
