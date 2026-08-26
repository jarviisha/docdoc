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

export async function send(plan: RequestPlan, body?: BodyInit): Promise<Response> {
  const response = await fetch(toUrl(plan), {
    method: plan.method,
    ...(plan.hasBody && body !== undefined ? { body } : {}),
  });

  return { ok: response.ok, body: await response.json() };
}
