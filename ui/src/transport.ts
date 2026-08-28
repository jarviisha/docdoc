/**
 * The only place this application touches the network.
 *
 * Neither model nor component. The model constructs a `RequestPlan` and the URL
 * it addresses — both pure, both tested — and this executes one. It contains no
 * decision: it does not choose a path, a method, a query or a body, and it must
 * not start.
 *
 * It exists because of what SC-013 actually claims: that across the full set of
 * user actions, zero requests are constructed that write to a store. That is
 * only a guarantee if construction happens in one tested place, and until T083
 * a component was still assembling the URL and calling `fetch` itself — so the
 * claim rested on nobody adding a second call site. `check-model-boundary.mjs`
 * now fails if one appears under `src/components/`.
 */

import { toUrl, type RequestPlan } from "./model/client.ts";

export interface Response {
  ok: boolean;
  body: unknown;
}

/**
 * A request that never produced a readable answer.
 *
 * Thrown so the caller can tell "the deployment reported a failure" from "we
 * never heard a usable answer" — two different things that reached the user as
 * one until T102. What the distinction *means* is `failure.ts`'s to say; this
 * only reports which happened, which is mechanics rather than a decision.
 */
export class TransportError extends Error {}

export async function send(plan: RequestPlan, body?: BodyInit): Promise<Response> {
  const response = await fetch(toUrl(plan), {
    method: plan.method,
    ...(plan.hasBody && body !== undefined ? { body } : {}),
  });

  try {
    return { ok: response.ok, body: await response.json() };
  } catch {
    // A proxy timing out mid-flight answers with HTML, not JSON. Letting the
    // `SyntaxError` escape presented a JSON parse failure to the user as the
    // *run's* cause — the deployment blamed for a body it never sent.
    throw new TransportError(
      `the deployment answered ${response.status} with a body this page could not read`,
    );
  }
}
