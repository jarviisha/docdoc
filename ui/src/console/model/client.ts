/**
 * Every request the console can make, constructed here and nowhere else.
 *
 * **Its own file, deliberately.** `src/model/client.ts` exists to prove a
 * different thing: its `ALLOWED` set holds two paths and `writesToStore()`
 * asserts that the viewer never writes. Adding console intents there would
 * weaken Milestone 8's guarantee in order to make this code shorter — the exact
 * trade `check-model-boundary.mjs` exists to prevent (specs/011 research R5).
 *
 * One transport, two clients. The transport keeps FR-041's single network call
 * site; the two clients keep two allow-lists, each of which says something true.
 *
 * **No intent puts the credential in a URL** (FR-008). It travels as a header,
 * supplied to `send` by the caller, because a URL lands in browser history,
 * proxy logs, and referrer headers.
 */

import type { RequestPlan } from "../../model/client.ts";

export type { RequestPlan };

/**
 * The tenant the administrative probe asks about.
 *
 * The console never learns its own tenant — no route reports it, and adding one
 * would be the second new read SC-003 forbids. It does not need to: the scope
 * check precedes the lookup, so a well-formed tenant that holds nothing answers
 * `200` with an empty list for an administrator and `404` for everyone else,
 * which is exactly the question being asked.
 */
export const PROBE_TENANT = "docdoc-scope-probe";

/** The five members `specs/009` closed the run status set at (FR-004). */
export const RUN_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"] as const;

export type RunStatus = (typeof RUN_STATUSES)[number];

export type Intent =
  /** The cheapest authenticated read there is. Used to find out whether the
   *  deployment authenticates at all (research R6) — not to list schemas. */
  | { type: "probe" }
  | { type: "list-runs"; status?: RunStatus; limit?: number; cursor?: string }
  | { type: "run-detail"; runId: string }
  | { type: "run-delivery"; runId: string }
  /**
   * Also the probe for administrative scope: a `404` means the area is absent
   * (research R7, FR-028).
   *
   * **`tenant` is required, and finding that out cost a container.** The route
   * takes `tenant_id` as a query parameter and answers `422` without it — so
   * the first version of this probe read "not an administrator" for an
   * administrator, and hid the credentials and erasure areas from the only
   * person who can use them. The scope check runs before the lookup, so any
   * well-formed tenant works for the probe and a nonexistent one returns an
   * empty list rather than somebody's data.
   */
  | { type: "list-credentials"; tenant: string }
  | { type: "issue-credential"; tenant: string; scope: string }
  | { type: "revoke-credential"; credentialId: string }
  | { type: "erase-tenant"; tenantId: string }
  | { type: "erase-document"; blobId: string };

/**
 * Every path shape this console knows. Anything else is a defect, not a feature.
 *
 * Patterns rather than strings because five of the nine address a resource. The
 * set is what `allIntents` is checked against, so that "the console reaches
 * exactly these nine endpoints" is a property a test can hold rather than a
 * sentence in a document.
 */
const ALLOWED = [
  /^\/v1\/schemas$/,
  /^\/v1\/runs$/,
  /^\/v1\/runs\/[^/]+$/,
  /^\/v1\/runs\/[^/]+\/delivery$/,
  /^\/v1\/admin\/credentials$/,
  /^\/v1\/admin\/credentials\/[^/]+$/,
  /^\/v1\/admin\/tenants\/[^/]+$/,
  /^\/v1\/admin\/documents\/[^/]+$/,
];

export function isAllowed(plan: RequestPlan): boolean {
  return ALLOWED.some((pattern) => pattern.test(plan.path));
}

/**
 * Which of this console's requests write.
 *
 * Kept as a predicate, exactly as the viewer's `writesToStore` is, so the rule
 * is stated once in the code and a test checks the rule rather than restating
 * it. The console **does** write — that is what Milestone 11 permits and what
 * Milestone 8 forbade — and every write here reaches a route Milestone 10
 * already ships (FR-027, FR-029). None of them is new.
 */
export function writes(plan: RequestPlan): boolean {
  return plan.method !== "GET";
}

export function requestFor(intent: Intent): RequestPlan {
  switch (intent.type) {
    case "probe":
      return { method: "GET", path: "/v1/schemas", query: {}, hasBody: false };

    case "list-runs": {
      // Absent rather than empty: a `status=` with nothing after it is a filter
      // nobody set, and the route would have to decide what that means.
      const query: Record<string, string> = {};
      if (intent.status !== undefined) query.status = intent.status;
      if (intent.limit !== undefined) query.limit = String(intent.limit);
      if (intent.cursor !== undefined) query.cursor = intent.cursor;
      return { method: "GET", path: "/v1/runs", query, hasBody: false };
    }

    case "run-detail":
      return {
        method: "GET",
        path: `/v1/runs/${encodeURIComponent(intent.runId)}`,
        query: {},
        hasBody: false,
      };

    case "run-delivery":
      return {
        method: "GET",
        path: `/v1/runs/${encodeURIComponent(intent.runId)}/delivery`,
        query: {},
        hasBody: false,
      };

    case "list-credentials":
      return {
        method: "GET",
        path: "/v1/admin/credentials",
        query: { tenant_id: intent.tenant },
        hasBody: false,
      };

    case "issue-credential":
      return { method: "POST", path: "/v1/admin/credentials", query: {}, hasBody: true };

    case "revoke-credential":
      return {
        method: "DELETE",
        path: `/v1/admin/credentials/${encodeURIComponent(intent.credentialId)}`,
        query: {},
        hasBody: false,
      };

    case "erase-tenant":
      return {
        method: "DELETE",
        path: `/v1/admin/tenants/${encodeURIComponent(intent.tenantId)}`,
        query: {},
        hasBody: false,
      };

    case "erase-document":
      return {
        method: "DELETE",
        path: `/v1/admin/documents/${encodeURIComponent(intent.blobId)}`,
        query: {},
        hasBody: false,
      };
  }
}

/**
 * Where a succeeded run's result can be read.
 *
 * A link and nothing more (FR-022a). The console does not fetch this, does not
 * parse it, and renders no part of it — `GET /v1/runs/{run_id}` already fixes
 * the rule this follows: one result representation, reachable one way. A console
 * that rendered values would be the second, with its own rounding, its own field
 * ordering, and its own opportunity to disagree with the first.
 */
// console-boundary-exempt: link only — this builds an href and reads nothing.
export function resultHref(processingId: string): string {
  return `/v1/jobs/${encodeURIComponent(processingId)}/result`;
}

/** Every intent a user of this console can express, for the coverage tests. */
export function allIntents(): Intent[] {
  return [
    { type: "probe" },
    { type: "list-runs" },
    { type: "list-runs", status: "failed", limit: 50, cursor: "abc" },
    { type: "run-detail", runId: "r" },
    { type: "run-delivery", runId: "r" },
    { type: "list-credentials", tenant: "acme" },
    { type: "issue-credential", tenant: "t", scope: "admin" },
    { type: "revoke-credential", credentialId: "c" },
    { type: "erase-tenant", tenantId: "t" },
    { type: "erase-document", blobId: "b" },
  ];
}
