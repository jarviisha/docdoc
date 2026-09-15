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
  /** The status, because `401` and `404` mean different things to a console. */
  status: number;
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

/**
 * What a caller supplies beyond the plan.
 *
 * `credential` arrives from the operations console, which holds an API key in
 * memory for the life of a page (Milestone 11 FR-006, FR-007). It is a
 * parameter and never a module-level variable: this file decides nothing, and a
 * credential it could reach on its own would be a credential it could attach to
 * a request nobody asked it to.
 *
 * It becomes a header, never a query parameter — FR-008, and the reason is that
 * a URL lands in browser history, proxy logs, and referrer headers.
 */
export interface Options {
  body?: BodyInit;
  credential?: string;
}

export async function send(plan: RequestPlan, options: Options = {}): Promise<Response> {
  const { body, credential } = options;
  const response = await fetch(toUrl(plan), {
    method: plan.method,
    ...(credential === undefined ? {} : { headers: { Authorization: `Bearer ${credential}` } }),
    ...(plan.hasBody && body !== undefined ? { body } : {}),
  });

  // `401` is not an error to this layer — it is an answer, and a well-formed
  // one. The console's session model is what decides that a credential was
  // refused; reporting it here would put that decision where no test reaches it.
  try {
    return { ok: response.ok, status: response.status, body: await response.json() };
  } catch {
    // A proxy timing out mid-flight answers with HTML, not JSON. Letting the
    // `SyntaxError` escape presented a JSON parse failure to the user as the
    // *run's* cause — the deployment blamed for a body it never sent.
    throw new TransportError(
      `the deployment answered ${response.status} with a body this page could not read`,
    );
  }
}
