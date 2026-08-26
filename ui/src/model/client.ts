/**
 * Every request this viewer can make, constructed here and nowhere else.
 *
 * Putting request construction in the model is what makes SC-013 measurable
 * without a browser: the claim "the viewer issues zero requests that write to a
 * store and constructs zero corrections" is checked by exercising this function
 * over the full set of user intents. A component calling `fetch` directly would
 * put that claim out of reach along with the rest.
 *
 * **The document goes to exactly one place.** `POST /v1/extract` persists
 * nothing (ADR-0012), so the bytes the user picked are never stored by the
 * deployment and never sent anywhere else — and never written to browser-
 * persistent storage either, which is why nothing here touches `localStorage`
 * (FR-032).
 */

export type Intent =
  | { type: "list-schemas" }
  | { type: "extract"; schema: string; document: Uint8Array };

export interface RequestPlan {
  method: "GET" | "POST";
  path: string;
  query: Record<string, string>;
  hasBody: boolean;
}

/** The only paths this viewer knows. Anything else is a defect, not a feature. */
const ALLOWED = new Set(["/v1/schemas", "/v1/extract"]);

export function requestFor(intent: Intent): RequestPlan {
  switch (intent.type) {
    case "list-schemas":
      return { method: "GET", path: "/v1/schemas", query: {}, hasBody: false };

    case "extract":
      // The storeless route, always. `POST /v1/documents` would store the
      // document, and `POST /v1/documents/{id}/extract` would require it to be
      // stored first — either is a write, and this viewer performs none.
      return {
        method: "POST",
        path: "/v1/extract",
        query: { schema: intent.schema },
        hasBody: true,
      };
  }
}

/**
 * Whether a plan writes anything the deployment keeps.
 *
 * Kept as a predicate rather than an assertion in a test so the rule is stated
 * once, in the code, and the test checks the rule rather than restating it.
 */
export function writesToStore(plan: RequestPlan): boolean {
  if (!ALLOWED.has(plan.path)) return true;
  // `/v1/extract` is a POST and persists nothing; that is the whole point of
  // ADR-0012. Any *other* POST would be a write.
  return plan.method === "POST" && plan.path !== "/v1/extract";
}

/**
 * The URL a plan addresses.
 *
 * Pure, and here rather than in the caller, because assembling a URL is a
 * decision about where a request goes — the last piece of request construction
 * that was still happening in a component before T083 moved it (SC-013,
 * FR-043). Executing the request is `src/transport.ts`, which decides nothing.
 */
export function toUrl(plan: RequestPlan): string {
  const query = new URLSearchParams(plan.query).toString();
  return query === "" ? plan.path : `${plan.path}?${query}`;
}

/** Every intent a user of this viewer can express (FR-029, FR-030, SC-013). */
export function allIntents(document: Uint8Array, schema: string): Intent[] {
  return [{ type: "list-schemas" }, { type: "extract", schema, document }];
}
