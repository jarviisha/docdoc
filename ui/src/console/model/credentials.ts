/**
 * Credentials: issue, list, revoke — through the console's own routes
 * (ADR-0023 §3), which read and write the same `docdoc.runs.credentials`
 * store Milestone 10's admin routes do.
 *
 * **No `scopes`, ever** (ADR-0023 §2). An operator's session is one browser
 * tab away from a key nobody meant to mint with the admin scope, so the body
 * this file builds never carries the question: `issueBody` sends
 * `{tenant_id}` or `{tenant_id, label}`, and the server refuses outright if a
 * `scopes` key is present at all.
 *
 * **The console cannot tell which listed credential is its own**, and that is
 * a decision rather than a gap (FR-026a) — this is also moot now in a sense
 * ADR-0019 §4 never had: a signed-in operator authenticates with the session
 * cookie, never with a tenant credential, so there is no "own key" among
 * these rows to begin with. The warning stays general on purpose, because a
 * credential revoked here may still be the one some other caller — mmerp, a
 * script, anything holding the key — is using right now.
 *
 * Refusing the action instead was rejected for a second reason — it would be
 * the console inventing a rule the API does not have, and blocking the
 * emergency the feature exists for. A leaked key is very often the key
 * somebody is still using.
 */

/**
 * The body `POST /v1/console/credentials` is sent, named by tenant and an
 * optional label — never a `scopes` key, which the server refuses outright if
 * present at all (ADR-0023 §2). An empty label is the same as none.
 */
export function issueBody(tenantId: string, label: string): string {
  const trimmed = label.trim();
  return JSON.stringify(trimmed === "" ? { tenant_id: tenantId } : { tenant_id: tenantId, label: trimmed });
}

/** A row of the listing. Never a key, and never a fragment of one (FR-025). */
export interface CredentialRecord {
  credential_id: string;
  tenant_id: string;
  label: string | null;
  /** Plural, as the route returns it. `[]` is an ordinary credential. */
  scopes: string[];
  created_at: string;
  last_used_at: string | null;
  revoked_at: string | null;
}

export function toRecords(body: unknown): CredentialRecord[] {
  if (typeof body !== "object" || body === null) return [];
  const listed = (body as { credentials?: unknown }).credentials;
  return Array.isArray(listed) ? (listed as CredentialRecord[]) : [];
}

/**
 * The key, held for exactly as long as it takes to be read once (FR-024).
 *
 * `null` means there is nothing to show, which is the state this returns to the
 * moment the operator acknowledges it. Nothing else in the console ever holds a
 * key besides the session's own.
 */
export interface Disclosure {
  key: string;
  credentialId: string;
}

export function disclosureFrom(body: unknown): Disclosure | null {
  if (typeof body !== "object" || body === null) return null;
  const key = (body as { key?: unknown }).key;
  const id = (body as { credential_id?: unknown }).credential_id;
  if (typeof key !== "string" || typeof id !== "string") return null;
  return { key, credentialId: id };
}

/** What is said beside the key, once, and in as many words. */
export const DISCLOSURE_WARNING =
  "This is the only time this key will be shown. Copy it now; it cannot be retrieved.";

/**
 * What is said before the first revocation of a console session (FR-026a).
 *
 * General, because the console cannot tell which credential anything else is
 * using and is not going to find out (ADR-0023 §2 moots the one case this used
 * to warn about specifically — the operator's own session is the cookie, never
 * one of these rows). Stated once per session rather than per revocation: an
 * operator revoking five leaked keys does not need to be told five times, and a
 * warning shown that often is one that is clicked through.
 */
export const SELF_REVOCATION_WARNING =
  "A revoked credential stops working immediately. If something — mmerp, a script, " +
  "anything holding the key — is still using it, that caller will start failing right away.";

export interface CredentialState {
  records: CredentialRecord[];
  disclosure: Disclosure | null;
  /** Whether the once-per-session warning has been shown. */
  warned: boolean;
  /** The tenant whose credentials are listed; `""` before the first Show. */
  shown: string;
  /** Where that listing stands — only `loaded` may say "holds none". */
  listing: "pending" | "loaded" | "failed";
}

export function initialCredentials(): CredentialState {
  return { records: [], disclosure: null, warned: false, shown: "", listing: "loaded" };
}

/** Show pressed: a new tenant's listing is on its way; the old rows go. */
export function showing(state: CredentialState, tenant: string): CredentialState {
  return { ...state, shown: tenant, records: [], listing: "pending" };
}

/**
 * A listing answered. Ignored when it is for a tenant no longer shown — a
 * slow answer for the previous Show must not overwrite the current one.
 */
export function listedFor(
  state: CredentialState,
  tenant: string,
  records: CredentialRecord[],
): CredentialState {
  return tenant === state.shown ? { ...state, records, listing: "loaded" } : state;
}

/** A listing failed. Ignored, like `listedFor`, for a tenant no longer shown. */
export function listFailedFor(state: CredentialState, tenant: string): CredentialState {
  return tenant === state.shown ? { ...state, records: [], listing: "failed" } : state;
}

/** What is said in place of the table, or `null` when there are rows to show. */
export function listingNote(state: CredentialState): string | null {
  if (state.shown === "") return "Name a tenant and choose Show to list its credentials.";
  if (state.listing === "pending") return "Listing credentials…";
  if (state.listing === "failed") return "The credentials for this tenant could not be listed.";
  return state.records.length === 0 ? "This tenant holds no credentials." : null;
}

export function withDisclosure(
  state: CredentialState,
  disclosure: Disclosure | null,
): CredentialState {
  return { ...state, disclosure };
}

/** Acknowledging the key drops it from memory. It is not stored anywhere else. */
export function acknowledged(state: CredentialState): CredentialState {
  return { ...state, disclosure: null };
}

export function warned(state: CredentialState): CredentialState {
  return { ...state, warned: true };
}

/** Whether this revocation needs the warning shown first. */
export function needsWarning(state: CredentialState): boolean {
  return !state.warned;
}

/** How a row's scopes read on screen. `—` rather than an empty cell. */
export function scopesOf(record: CredentialRecord): string {
  return record.scopes.length === 0 ? "—" : record.scopes.join(", ");
}

/**
 * Whether a row is still usable.
 *
 * Derived rather than stored, because `revoked_at` is the fact the deployment
 * records and "active" is a reading of it. Two fields would eventually disagree.
 */
export function isActive(record: CredentialRecord): boolean {
  return record.revoked_at === null;
}
