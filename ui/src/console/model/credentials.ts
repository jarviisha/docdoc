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
  /**
   * Which tenant `records` describes, or `null` if nothing has been listed.
   *
   * The listing used to run from an effect keyed on the field, so the rows were
   * never more than a keystroke behind what was typed and this could stay
   * implicit. Asking explicitly opened the gap between them, and an empty array
   * that means *"not looked up"* rendered identically to one that means *"this
   * tenant holds nothing"* — the same conflation `runs.ts`'s three listing
   * states exist to prevent, arrived at from the other direction.
   */
  listed: string | null;
  disclosure: Disclosure | null;
  /** Whether the once-per-session warning has been shown. */
  warned: boolean;
  /**
   * The credential a revocation has been asked about but not yet performed.
   *
   * Revocation is irreversible and used to happen on the click — one control,
   * one mis-aimed pointer, one key gone. The erasure area has typed
   * confirmation for the same reason; this is the lighter form of it, because
   * a revoked credential costs its holder a reconnection and an erased tenant
   * costs a customer their documents.
   */
  pending: string | null;
}

export function initialCredentials(): CredentialState {
  return { records: [], listed: null, disclosure: null, warned: false, pending: null };
}

export function withRecords(
  state: CredentialState,
  records: CredentialRecord[],
  tenant: string,
): CredentialState {
  return { ...state, records, listed: tenant };
}

/** Whether the rows in hand describe the tenant currently named. */
export function describes(state: CredentialState, typed: string): boolean {
  return state.listed !== null && state.listed === typed.trim();
}

/**
 * What stands where the rows would be, or `null` when the rows are there.
 *
 * "This tenant holds no credentials" is a statement about a deployment and may
 * only be made about a tenant that was actually asked about. Everything else —
 * an empty field, a half-typed name, rows left over from the previous tenant —
 * is the console having nothing to say yet, and it says that instead.
 */
export function listingNotice(state: CredentialState, typed: string): string | null {
  if (!describes(state, typed)) return "Name a tenant and list its credentials.";
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

/** A revocation has been asked about. Nothing has been sent. */
export function askedToRevoke(state: CredentialState, credentialId: string): CredentialState {
  return { ...state, pending: credentialId };
}

/** Asked and thought better of. The warning stays unspent — see below. */
export function revocationCancelled(state: CredentialState): CredentialState {
  return { ...state, pending: null };
}

/**
 * Confirmed, and this is where the once-per-session warning is spent.
 *
 * **Not when the confirmation opens.** FR-026a warns before the first
 * *revocation*, and an operator who opened the dialog and backed out has not
 * performed one — marking it shown there would mean the next attempt, the one
 * they go through with, is the first unwarned one.
 */
export function revocationConfirmed(state: CredentialState): CredentialState {
  return { ...state, pending: null, warned: true };
}

/**
 * What the confirmation says will happen.
 *
 * The self-revocation warning is part of it rather than a banner over the
 * table, which is where it used to be: it was rendered at the moment the
 * request was already in flight, so "before the first revocation" was true by
 * a few milliseconds and false in every sense an operator would recognise.
 * Here it is on the screen that is asking.
 */
export function revocationPrompt(state: CredentialState): string {
  const consequence =
    "This cannot be undone. Anything using this credential stops being able to reach the deployment.";
  return needsWarning(state) ? `${consequence} ${SELF_REVOCATION_WARNING}` : consequence;
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
