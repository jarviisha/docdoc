/**
 * The URL search params each routed area accepts, parsed from whatever the
 * address bar held. A bad value never throws: it is omitted, so a hand-edited
 * or stale link opens the area unfiltered instead of failing.
 */

import { RUN_STATUSES, type RunStatus } from "./client.ts";
import type { RunKind } from "./runs.ts";

export interface RunsSearch {
  tenant?: string;
  status?: RunStatus;
  kind?: RunKind;
}

export interface TenantSearch {
  tenant?: string;
}

export interface QualitySearch {
  tenant?: string;
  schema?: string;
  evaluation?: string;
}

/** A non-empty trimmed string, else undefined. */
function text(value: unknown): string | undefined {
  if (typeof value !== "string") return undefined;
  return value.trim() || undefined;
}

/** `value` if it is one of `allowed`, else undefined. */
function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T | undefined {
  const v = text(value);
  return allowed.find((a) => a === v);
}

/** Copy only the defined entries: `exactOptionalPropertyTypes` forbids `undefined` keys. */
function defined<T extends object>(entries: { [K in keyof T]-?: T[K] | undefined }): T {
  return Object.fromEntries(Object.entries(entries).filter(([, v]) => v !== undefined)) as T;
}

export function runsSearch(raw: Record<string, unknown>): RunsSearch {
  return defined<RunsSearch>({
    tenant: text(raw.tenant),
    status: oneOf(raw.status, RUN_STATUSES),
    kind: oneOf(raw.kind, ["ocr", "extraction"] as const),
  });
}

/** Credentials, callbacks and erasure: scoped to one tenant, nothing else. */
export function tenantSearch(raw: Record<string, unknown>): TenantSearch {
  return defined<TenantSearch>({ tenant: text(raw.tenant) });
}

export function qualitySearch(raw: Record<string, unknown>): QualitySearch {
  return defined<QualitySearch>({
    tenant: text(raw.tenant),
    schema: text(raw.schema),
    evaluation: text(raw.evaluation),
  });
}

/**
 * The router's default parser JSON-decodes each value, so `?tenant=123` would
 * arrive as a number and `text()` would drop it. Tenant ids such as `123`,
 * `null` or `1e5` are valid, so every value stays the string the URL held.
 */
export function parseQuery(search: string): Record<string, string> {
  return Object.fromEntries(new URLSearchParams(search));
}

/** The inverse of `parseQuery`: no JSON quoting, empty and undefined values left out. */
export function stringifyQuery(params: Record<string, unknown>): string {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") query.set(key, String(value));
  }
  const text = query.toString();
  return text === "" ? "" : `?${text}`;
}
