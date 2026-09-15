/**
 * Credentials: issue, list, revoke — through Milestone 10's routes and no others.
 *
 * **The console cannot tell which listed credential is its own**, and that is a
 * decision rather than a gap (FR-026a). Knowing would need a route returning the
 * caller's credential identity, which is a second new read and costs the claim
 * SC-003 measures. So revoking the key in use is permitted, warned about once,
 * and then handled by the path a refusal already takes: the next action fails,
 * the session drops the key, the console asks for another.
 *
 * Refusing the action instead was rejected for a second reason — it would be the
 * console inventing a rule the API does not have, and blocking the emergency the
 * feature exists for. A leaked key is very often the key you are holding.
 */

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
 * What is said before the first revocation of a session (FR-026a).
 *
 * General, because the console does not know which credential is its own and is
 * not going to find out. Stated once per session rather than per revocation: an
 * operator revoking five leaked keys does not need to be told five times, and a
 * warning shown that often is one that is clicked through.
 */
export const SELF_REVOCATION_WARNING =
  "A revocation may include the key you are using. If it does, you will be asked to enter another.";

export interface CredentialState {
  records: CredentialRecord[];
  disclosure: Disclosure | null;
  /** Whether the once-per-session warning has been shown. */
  warned: boolean;
}

export function initialCredentials(): CredentialState {
  return { records: [], disclosure: null, warned: false };
}

export function withRecords(
  state: CredentialState,
  records: CredentialRecord[],
): CredentialState {
  return { ...state, records };
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
