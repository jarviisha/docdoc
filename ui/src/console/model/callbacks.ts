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
