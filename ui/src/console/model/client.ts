/**
 * Every request the console can make, constructed here and nowhere else.
 *
 * ADR-0023 §3 gives the console its own route family: every path below is
 * under `/v1/console`, answers only to the session cookie, and is reached
 * with no credential of this file's own — `src/transport.ts` sends the
 * cookie because the browser does, not because anything here hands it one.
 *
 * `src/transport.ts` executes a plan and decides nothing, which is FR-041's
 * single network call site.
 */

import type { RequestPlan } from "../../transport.ts";
import type { RunKind } from "./runs.ts";

export type { RequestPlan };

/** The five members `specs/009` closed the run status set at (FR-004). */
export const RUN_STATUSES = ["queued", "running", "succeeded", "failed", "cancelled"] as const;

export type RunStatus = (typeof RUN_STATUSES)[number];

export type Intent =
  | { type: "who-am-i" }
  | { type: "sign-in" }
  | { type: "sign-out" }
  | { type: "overview" }
  | {
      type: "list-runs";
      tenant?: string;
      status?: RunStatus;
      kind?: RunKind;
      limit?: number;
      cursor?: string;
    }
  | { type: "run-detail"; runId: string }
  | { type: "run-delivery"; runId: string }
  | { type: "run-result"; runId: string }
  /** A page's rendered image, `page` 0-based (`GET …/pages/{page}`). */
  | { type: "run-page"; runId: string; page: number }
  | { type: "cancel-run"; runId: string }
  | { type: "list-credentials"; tenant: string }
  | { type: "issue-credential" }
  | { type: "revoke-credential"; credentialId: string }
  | { type: "run-checks"; runId: string }
  | { type: "record-check"; runId: string }
  | { type: "tenant-answers"; tenantId: string }
  | { type: "start-evaluation"; tenantId: string; schema: string }
  | { type: "list-evaluations"; tenantId: string; schema?: string }
  | { type: "evaluation"; tenantId: string; evaluationId: string }
  | { type: "erase-tenant"; tenantId: string }
  | { type: "erase-document"; tenantId: string; blobId: string }
  | { type: "list-tenants" }
  | { type: "list-parsers" }
  | { type: "list-schemas" }
  | { type: "schema-detail"; identity: string }
  | { type: "create-schema" }
  /** `tenantId` null is the deployment default: no `tenant` query. */
  | { type: "list-providers"; tenantId: string | null }
  | { type: "set-provider-priority"; tenantId: string | null }
  | { type: "set-provider"; tenantId: string | null; adapterId: string }
  | { type: "reset-providers"; tenantId: string }
  | { type: "test-provider"; tenantId: string | null; adapterId: string }
  | { type: "list-callbacks"; tenantId: string }
  | { type: "register-callback"; tenantId: string }
  | { type: "revoke-callback"; tenantId: string; callbackId: string }
  | {
      type: "submit-ocr";
      tenantId: string;
      parser?: string;
      language?: string;
      /** Sent as `Idempotency-Key`, so a resend after a lost answer replays the run. */
      idempotencyKey?: string;
    }
  | { type: "submit-extraction"; tenantId: string; schema: string; idempotencyKey?: string };

/**
 * Every path shape this console knows. Anything else is a defect, not a feature.
 *
 * The set is what `allIntents` is checked against, so that "the console reaches
 * exactly these endpoints, all under `/v1/console`" is a property a test can
 * hold rather than a sentence in a document.
 */
const ALLOWED = [
  /^\/v1\/console\/session$/,
  /^\/v1\/console\/overview$/,
  /^\/v1\/console\/runs$/,
  /^\/v1\/console\/runs\/[^/]+$/,
  /^\/v1\/console\/runs\/[^/]+\/delivery$/,
  /^\/v1\/console\/runs\/[^/]+\/result$/,
  /^\/v1\/console\/runs\/[^/]+\/pages\/\d+$/,
  /^\/v1\/console\/runs\/[^/]+\/corrections$/,
  /^\/v1\/console\/tenants\/[^/]+\/answers$/,
  /^\/v1\/console\/tenants\/[^/]+\/evaluations$/,
  /^\/v1\/console\/tenants\/[^/]+\/evaluations\/[^/]+$/,
  /^\/v1\/console\/credentials$/,
  /^\/v1\/console\/credentials\/[^/]+$/,
  /^\/v1\/console\/tenants$/,
  /^\/v1\/console\/tenants\/[^/]+$/,
  /^\/v1\/console\/tenants\/[^/]+\/documents\/[^/]+$/,
  /^\/v1\/console\/tenants\/[^/]+\/ocr$/,
  /^\/v1\/console\/parsers$/,
  /^\/v1\/console\/schemas$/,
  /^\/v1\/console\/schemas\/[^/]+$/,
  /^\/v1\/console\/providers$/,
  /^\/v1\/console\/providers\/[^/]+$/,
  /^\/v1\/console\/providers\/[^/]+\/test$/,
  /^\/v1\/console\/tenants\/[^/]+\/extractions$/,
  /^\/v1\/console\/tenants\/[^/]+\/callbacks$/,
  /^\/v1\/console\/tenants\/[^/]+\/callbacks\/[^/]+$/,
];

export function isAllowed(plan: RequestPlan): boolean {
  return ALLOWED.some((pattern) => pattern.test(plan.path));
}

export function requestFor(intent: Intent): RequestPlan {
  switch (intent.type) {
    case "who-am-i":
      return { method: "GET", path: "/v1/console/session", query: {}, hasBody: false };

    case "sign-in":
      return { method: "POST", path: "/v1/console/session", query: {}, hasBody: true };

    case "sign-out":
      return { method: "DELETE", path: "/v1/console/session", query: {}, hasBody: false };

    case "overview":
      return { method: "GET", path: "/v1/console/overview", query: {}, hasBody: false };

    case "list-runs": {
      // Absent rather than empty: a `status=` with nothing after it is a filter
      // nobody set, and the route would have to decide what that means.
      const query: Record<string, string> = {};
      if (intent.tenant !== undefined) query.tenant = intent.tenant;
      if (intent.status !== undefined) query.status = intent.status;
      if (intent.kind !== undefined) query.kind = intent.kind;
      if (intent.limit !== undefined) query.limit = String(intent.limit);
      if (intent.cursor !== undefined) query.cursor = intent.cursor;
      return { method: "GET", path: "/v1/console/runs", query, hasBody: false };
    }

    case "run-detail":
      return {
        method: "GET",
        path: `/v1/console/runs/${encodeURIComponent(intent.runId)}`,
        query: {},
        hasBody: false,
      };

    case "run-delivery":
      return {
        method: "GET",
        path: `/v1/console/runs/${encodeURIComponent(intent.runId)}/delivery`,
        query: {},
        hasBody: false,
      };

    case "run-result":
      return {
        method: "GET",
        path: `/v1/console/runs/${encodeURIComponent(intent.runId)}/result`,
        query: {},
        hasBody: false,
      };

    case "run-page":
      return {
        method: "GET",
        path: `/v1/console/runs/${encodeURIComponent(intent.runId)}/pages/${intent.page}`,
        query: {},
        hasBody: false,
      };

    case "run-checks":
    case "record-check":
      return {
        method: intent.type === "run-checks" ? "GET" : "POST",
        path: `/v1/console/runs/${encodeURIComponent(intent.runId)}/corrections`,
        query: {},
        hasBody: intent.type === "record-check",
      };

    case "tenant-answers":
      return {
        method: "GET",
        path: `/v1/console/tenants/${encodeURIComponent(intent.tenantId)}/answers`,
        query: {},
        hasBody: false,
      };

    case "start-evaluation":
      return {
        method: "POST",
        path: `/v1/console/tenants/${encodeURIComponent(intent.tenantId)}/evaluations`,
        query: { schema: intent.schema },
        hasBody: false,
      };

    case "list-evaluations":
      return {
        method: "GET",
        path: `/v1/console/tenants/${encodeURIComponent(intent.tenantId)}/evaluations`,
        query: intent.schema === undefined ? {} : { schema: intent.schema },
        hasBody: false,
      };

    case "evaluation":
      return {
        method: "GET",
        path:
          `/v1/console/tenants/${encodeURIComponent(intent.tenantId)}` +
          `/evaluations/${encodeURIComponent(intent.evaluationId)}`,
        query: {},
        hasBody: false,
      };

    case "cancel-run":
      return {
        method: "DELETE",
        path: `/v1/console/runs/${encodeURIComponent(intent.runId)}`,
        query: {},
        hasBody: false,
      };

    case "list-credentials":
      return {
        method: "GET",
        path: "/v1/console/credentials",
        query: { tenant: intent.tenant },
        hasBody: false,
      };

    case "issue-credential":
      return { method: "POST", path: "/v1/console/credentials", query: {}, hasBody: true };

    case "revoke-credential":
      return {
        method: "DELETE",
        path: `/v1/console/credentials/${encodeURIComponent(intent.credentialId)}`,
        query: {},
        hasBody: false,
      };

    case "erase-tenant":
      return {
        method: "DELETE",
        path: `/v1/console/tenants/${encodeURIComponent(intent.tenantId)}`,
        query: {},
        hasBody: false,
      };

    case "erase-document":
      return {
        method: "DELETE",
        path:
          `/v1/console/tenants/${encodeURIComponent(intent.tenantId)}` +
          `/documents/${encodeURIComponent(intent.blobId)}`,
        query: {},
        hasBody: false,
      };

    case "list-tenants":
      return { method: "GET", path: "/v1/console/tenants", query: {}, hasBody: false };

    case "list-parsers":
      return { method: "GET", path: "/v1/console/parsers", query: {}, hasBody: false };

    case "list-schemas":
      return { method: "GET", path: "/v1/console/schemas", query: {}, hasBody: false };

    case "schema-detail":
      return {
        method: "GET",
        path: `/v1/console/schemas/${encodeURIComponent(intent.identity)}`,
        query: {},
        hasBody: false,
      };

    case "create-schema":
      return { method: "POST", path: "/v1/console/schemas", query: {}, hasBody: true };

    case "list-providers":
    case "set-provider-priority":
    case "set-provider":
    case "reset-providers":
    case "test-provider": {
      const adapter =
        "adapterId" in intent ? `/${encodeURIComponent(intent.adapterId)}` : "";
      const path =
        intent.type === "set-provider-priority"
          ? "/priority"
          : intent.type === "test-provider"
            ? `${adapter}/test`
            : adapter;
      return {
        method:
          intent.type === "list-providers"
            ? "GET"
            : intent.type === "reset-providers"
              ? "DELETE"
              : intent.type === "test-provider"
                ? "POST"
                : "PUT",
        path: `/v1/console/providers${path}`,
        query: intent.tenantId === null ? {} : { tenant: intent.tenantId },
        hasBody: intent.type !== "list-providers" && intent.type !== "reset-providers",
      };
    }

    case "submit-extraction":
      return {
        method: "POST",
        path: `/v1/console/tenants/${encodeURIComponent(intent.tenantId)}/extractions`,
        query: { schema: intent.schema },
        hasBody: true,
        ...(intent.idempotencyKey === undefined
          ? {}
          : { headers: { "Idempotency-Key": intent.idempotencyKey } }),
      };

    case "list-callbacks":
      return {
        method: "GET",
        path: `/v1/console/tenants/${encodeURIComponent(intent.tenantId)}/callbacks`,
        query: {},
        hasBody: false,
      };

    case "register-callback":
      return {
        method: "POST",
        path: `/v1/console/tenants/${encodeURIComponent(intent.tenantId)}/callbacks`,
        query: {},
        hasBody: true,
      };

    case "revoke-callback":
      return {
        method: "DELETE",
        path:
          `/v1/console/tenants/${encodeURIComponent(intent.tenantId)}` +
          `/callbacks/${encodeURIComponent(intent.callbackId)}`,
        query: {},
        hasBody: false,
      };

    case "submit-ocr": {
      // Absent rather than empty, as `list-runs` already does: a `parser=`
      // with nothing after it is a choice nobody made, and the route would
      // have to decide what that means.
      const query: Record<string, string> = {};
      if (intent.parser !== undefined) query.parser = intent.parser;
      if (intent.language !== undefined) query.language = intent.language;
      return {
        method: "POST",
        path: `/v1/console/tenants/${encodeURIComponent(intent.tenantId)}/ocr`,
        query,
        hasBody: true,
        ...(intent.idempotencyKey === undefined
          ? {}
          : { headers: { "Idempotency-Key": intent.idempotencyKey } }),
      };
    }
  }
}

/** Every intent a user of this console can express, for the coverage tests. */
export function allIntents(): Intent[] {
  return [
    { type: "who-am-i" },
    { type: "sign-in" },
    { type: "sign-out" },
    { type: "overview" },
    { type: "list-runs" },
    {
      type: "list-runs",
      tenant: "acme",
      status: "failed",
      kind: "ocr",
      limit: 50,
      cursor: "abc",
    },
    { type: "run-detail", runId: "r" },
    { type: "run-delivery", runId: "r" },
    { type: "run-result", runId: "r" },
    { type: "run-page", runId: "r", page: 0 },
    { type: "cancel-run", runId: "r" },
    { type: "list-credentials", tenant: "acme" },
    { type: "issue-credential" },
    { type: "revoke-credential", credentialId: "c" },
    { type: "run-checks", runId: "r" },
    { type: "record-check", runId: "r" },
    { type: "tenant-answers", tenantId: "t" },
    { type: "start-evaluation", tenantId: "t", schema: "s" },
    { type: "list-evaluations", tenantId: "t" },
    { type: "list-evaluations", tenantId: "t", schema: "s" },
    { type: "evaluation", tenantId: "t", evaluationId: "e" },
    { type: "erase-tenant", tenantId: "t" },
    { type: "erase-document", tenantId: "t", blobId: "b" },
    { type: "list-tenants" },
    { type: "list-parsers" },
    { type: "list-callbacks", tenantId: "acme" },
    { type: "register-callback", tenantId: "acme" },
    { type: "revoke-callback", tenantId: "acme", callbackId: "c" },
    { type: "submit-ocr", tenantId: "t" },
    { type: "list-schemas" },
    { type: "schema-detail", identity: "invoice@2" },
    { type: "create-schema" },
    { type: "list-providers", tenantId: null },
    { type: "list-providers", tenantId: "acme" },
    { type: "set-provider-priority", tenantId: null },
    { type: "set-provider", tenantId: "acme", adapterId: "openai" },
    { type: "reset-providers", tenantId: "acme" },
    { type: "test-provider", tenantId: null, adapterId: "openai" },
    { type: "submit-extraction", tenantId: "t", schema: "s" },
  ];
}
