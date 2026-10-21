/**
 * Callbacks: register and revoke a tenant's delivery endpoint, through the
 * console's own routes — which reuse the deliverer's own `register` and
 * `revoke` (contract-and-constraints.md) and copy none of its validation.
 *
 * **The secret is never returned or logged, on any route, list or log line**
 * (FR-066). `CallbackRecord` has no secret field to begin with, and
 * `toCallbacks` only ever builds one from the four names it knows — a row
 * answering with a `secret` or `secret_digest` besides loses them here,
 * before anything in this console could hold or show one.
 */

import { describe, type Failure } from "./failure.ts";

/** One row of `GET …/callbacks`. Never a secret, or a fragment of one. */
export interface CallbackRecord {
  callback_id: string;
  tenant_id: string;
  url: string;
  created_at: string;
}

/** Read the callback list from a response body without trusting its shape. */
export function toCallbacks(body: unknown): CallbackRecord[] {
  if (typeof body !== "object" || body === null) return [];
  const listed = (body as { callbacks?: unknown }).callbacks;
  if (!Array.isArray(listed)) return [];

  const records: CallbackRecord[] = [];
  for (const row of listed) {
    if (typeof row !== "object" || row === null) continue;
    const record = row as {
      callback_id?: unknown;
      tenant_id?: unknown;
      url?: unknown;
      created_at?: unknown;
    };
    if (
      typeof record.callback_id !== "string" ||
      typeof record.tenant_id !== "string" ||
      typeof record.url !== "string" ||
      typeof record.created_at !== "string"
    ) {
      continue;
    }
    records.push({
      callback_id: record.callback_id,
      tenant_id: record.tenant_id,
      url: record.url,
      created_at: record.created_at,
    });
  }
  return records;
}

/** The body `POST …/callbacks` is sent: both fields trimmed, and nothing else. */
export function registerBody(url: string, secret: string): string {
  return JSON.stringify({ url: url.trim(), secret: secret.trim() });
}

/** Whether Register may be pressed: a url and a secret, trimmed, and no request already under way. */
export function canRegister(url: string, secret: string, sending: boolean): boolean {
  return url.trim() !== "" && secret.trim() !== "" && !sending;
}

/** Whether Revoke may be pressed: no request already under way. */
export function canRevoke(sending: boolean): boolean {
  return !sending;
}

/**
 * The list as the area holds it: whose it is, its rows (`null` until one
 * answers), and the failure that answered instead.
 *
 * The tenant travels with the rows so a late answer for a tenant no longer
 * chosen is recognised and dropped, as `credentials.ts`'s `listedFor` does —
 * otherwise switching A → B quickly lets A's rows land under B's name.
 */
export interface CallbackListing {
  tenant: string;
  callbacks: CallbackRecord[] | null;
  failure: Failure | null;
}

/** A tenant chosen: its list is on its way, and the previous tenant's rows and failure go. */
export function choosing(tenant: string): CallbackListing {
  return { tenant, callbacks: null, failure: null };
}

/** A list answered. Ignored when it is for a tenant no longer chosen. */
export function listedFor(
  listing: CallbackListing,
  tenant: string,
  callbacks: CallbackRecord[],
): CallbackListing {
  return tenant === listing.tenant ? { ...listing, callbacks, failure: null } : listing;
}

/** A list failed. Ignored, like `listedFor`, when it is for a tenant no longer chosen. */
export function listFailedFor(
  listing: CallbackListing,
  tenant: string,
  failure: Failure,
): CallbackListing {
  return tenant === listing.tenant ? { ...listing, callbacks: null, failure } : listing;
}

/** The 503's sentence — this deployment delivers no callbacks — said in place of the table. */
export function unavailableNote(listing: CallbackListing): string | null {
  return listing.failure?.kind === "unavailable" ? describe(listing.failure) : null;
}

/** Any other list failure, in the deployment's own words. */
export function listProblem(listing: CallbackListing): string | null {
  return listing.failure === null || listing.failure.kind === "unavailable"
    ? null
    : describe(listing.failure);
}
