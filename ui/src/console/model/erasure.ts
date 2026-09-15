/**
 * Erasure: a named target, and the operator typing it back.
 *
 * **The comparison is here and not in the dialog**, because it is one `===` away
 * from being decorative and it is the only thing standing between a mis-click
 * and a customer's data (FR-030). A decision made inside a component is a
 * requirement with no coverage — the rendering layer carries no automated test.
 *
 * The counts a completed erasure reports are the deployment's own, passed
 * through untouched (FR-031). Nothing here sums, estimates, or rounds: a number
 * this console computed would be a second opinion about what was removed, and
 * the deployment's is the one that is true.
 */

export type Target = { kind: "tenant"; id: string } | { kind: "document"; id: string };

export interface ErasureIntent {
  target: Target;
  /** What the operator typed back. Compared character for character. */
  typed: string;
}

export function intendedFor(target: Target): ErasureIntent {
  return { target, typed: "" };
}

export function retyped(intent: ErasureIntent, typed: string): ErasureIntent {
  return { ...intent, typed };
}

/**
 * Whether the action may be offered at all.
 *
 * Exact, with no trimming and no case folding. A tenant identifier is
 * `[a-z0-9_-]{1,64}` and a blob identity is a hash — neither has a form where
 * "close enough" is a kindness, and an erasure that proceeds on a near miss is
 * the accident this exists to prevent.
 */
export function armed(intent: ErasureIntent): boolean {
  return intent.typed !== "" && intent.typed === intent.target.id;
}

/** What the operator is asked to do, in one sentence. */
export function confirmationPrompt(intent: ErasureIntent): string {
  const noun = intent.target.kind === "tenant" ? "tenant" : "document";
  return `Type the ${noun} identifier to confirm: ${intent.target.id}`;
}

/** The counts a completed erasure reported, in the deployment's own words. */
export interface Removed {
  [what: string]: number;
}

export function removedFrom(body: unknown): Removed {
  if (typeof body !== "object" || body === null) return {};
  const counts: Removed = {};
  for (const [what, value] of Object.entries(body as Record<string, unknown>)) {
    if (typeof value === "number") counts[what] = value;
  }
  return counts;
}

/**
 * What an erasure that removed nothing says.
 *
 * A target that does not exist is a **success** — the routes are idempotent and
 * say so — and presenting that as a failure would teach an operator to retry an
 * operation that already did everything it was going to do (FR-032).
 */
export function outcomeNotice(removed: Removed): string {
  const total = Object.values(removed).reduce((sum, count) => sum + count, 0);
  if (total === 0) {
    return "Nothing was there to remove. The erasure succeeded.";
  }
  return Object.entries(removed)
    .map(([what, count]) => `${count} ${what}`)
    .join(", ");
}
