/**
 * A run's delivery record: whether anyone was told, and what they said.
 *
 * Three states rather than a table that is sometimes empty, because "no
 * destination was registered" and "a destination was registered and nothing has
 * been attempted yet" are different answers to the operator's actual question,
 * which is *why did my client never hear about this* (FR-034).
 *
 * **No signing secret reaches this model** (FR-035). The route does not return
 * one; nothing here would have somewhere to put it if it did.
 */

/** One attempt, as Milestone 10 recorded it. */
export interface Attempt {
  attempted_at: string;
  /** The receiver's status, or `null` when the attempt never got an answer. */
  status: number | null;
  outcome: string;
}

export type Delivery =
  /** No callback was registered when the run finished. Nothing was owed. */
  | { state: "none" }
  /** Attempts have been made and more may follow. */
  | { state: "attempting"; attempts: Attempt[] }
  /** The delivery has come to rest, delivered or exhausted (ADR-0018). */
  | { state: "at-rest"; attempts: Attempt[]; delivered: boolean };

export function toDelivery(body: unknown): Delivery {
  if (typeof body !== "object" || body === null) return { state: "none" };

  const record = body as {
    callback_id?: unknown;
    attempts?: unknown;
    status?: unknown;
  };

  if (record.callback_id === undefined || record.callback_id === null) return { state: "none" };

  const attempts = Array.isArray(record.attempts) ? (record.attempts as Attempt[]) : [];
  const status = typeof record.status === "string" ? record.status : "";

  if (status === "delivered") return { state: "at-rest", attempts, delivered: true };
  if (status === "exhausted" || status === "failed") {
    return { state: "at-rest", attempts, delivered: false };
  }
  return { state: "attempting", attempts };
}

/** One sentence, so an empty table never has to be read as an answer. */
export function summarise(delivery: Delivery): string {
  switch (delivery.state) {
    case "none":
      return "No destination was registered for this run, so nothing was sent.";
    case "attempting":
      return `${delivery.attempts.length} attempt(s) so far; delivery is still being retried.`;
    case "at-rest":
      return delivery.delivered
        ? `Delivered after ${delivery.attempts.length} attempt(s).`
        : `Not delivered. ${delivery.attempts.length} attempt(s) were made and the delivery has come to rest.`;
  }
}

/**
 * What this console does **not** offer, recorded where someone would add it.
 *
 * No re-delivery, no cancellation, no editing a destination's address (FR-036).
 * Delivery is at-least-once and comes to rest by itself; a manual re-send is a
 * second delivery path with its own ordering questions, and it belongs to a
 * milestone that answers them rather than to the screen that noticed it was
 * missing.
 */
export const NOT_OFFERED = ["re-delivery", "cancellation", "destination editing"] as const;
