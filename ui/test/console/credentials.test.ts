/**
 * T047 — the key is shown once, the listing never carries one, and the console
 * does not know which credential is its own.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DISCLOSURE_WARNING,
  SELF_REVOCATION_WARNING,
  acknowledged,
  askedToRevoke,
  describes,
  disclosureFrom,
  initialCredentials,
  isActive,
  listingNotice,
  needsWarning,
  revocationCancelled,
  revocationConfirmed,
  revocationPrompt,
  scopesOf,
  toRecords,
  warned,
  withDisclosure,
  withRecords,
} from "../../src/console/model/credentials.ts";

test("an issued key is held once and dropped on acknowledgement", () => {
  const disclosure = disclosureFrom({ key: "sk-live-1", credential_id: "c1" });
  assert.deepEqual(disclosure, { key: "sk-live-1", credentialId: "c1" });

  const state = withDisclosure(initialCredentials(), disclosure);
  assert.notEqual(state.disclosure, null);

  const after = acknowledged(state);
  assert.equal(after.disclosure, null, "the key is still in memory after acknowledgement");
});

test("the disclosure says it is the only time", () => {
  assert.match(DISCLOSURE_WARNING, /only time/);
});

test("a body with no key discloses nothing", () => {
  assert.equal(disclosureFrom({ credential_id: "c1" }), null);
  assert.equal(disclosureFrom(null), null);
});

test("no listing row carries a key or a fragment of one", () => {
  const records = toRecords({
    credentials: [
      {
        credential_id: "c1",
        tenant_id: "acme",
        label: null,
        scopes: ["admin"],
        created_at: "2026-09-11T00:00:00Z",
        last_used_at: null,
        revoked_at: null,
      },
    ],
  });

  assert.equal(records.length, 1);
  for (const field of Object.keys(records[0] as object)) {
    assert.doesNotMatch(field, /^key$|secret|token/i);
  }
});

test("active is derived from the fact the deployment records", () => {
  const base = {
    credential_id: "c1",
    tenant_id: "acme",
    label: null,
    scopes: [] as string[],
    created_at: "2026-09-11T00:00:00Z",
    last_used_at: null,
  };
  assert.equal(isActive({ ...base, revoked_at: null }), true);
  assert.equal(isActive({ ...base, revoked_at: "2026-09-11T01:00:00Z" }), false);
});

test("the self-revocation warning is general and shown once per session", () => {
  // FR-026a. Identifying the console's own credential needs a route returning
  // the caller's identity — a second new read, which SC-003 forbids. So the
  // warning names the possibility rather than the row.
  assert.doesNotMatch(SELF_REVOCATION_WARNING, /this credential|that row|your key is/i);
  assert.match(SELF_REVOCATION_WARNING, /may include/);

  const state = initialCredentials();
  assert.equal(needsWarning(state), true);
  assert.equal(needsWarning(warned(state)), false);
});

test("scopes read as the route sends them, and an empty set is a dash", () => {
  const base = {
    credential_id: "c1",
    tenant_id: "acme",
    label: null,
    created_at: "2026-09-11T00:00:00Z",
    last_used_at: null,
    revoked_at: null,
  };
  assert.equal(scopesOf({ ...base, scopes: [] }), "—");
  assert.equal(scopesOf({ ...base, scopes: ["admin"] }), "admin");
});

test("the records replace rather than accumulate", () => {
  const one = withRecords(initialCredentials(), [{ credential_id: "a" } as never], "acme");
  const two = withRecords(one, [{ credential_id: "b" } as never], "acme");
  assert.equal(two.records.length, 1);
});

test("nothing is claimed about a tenant nobody asked about", () => {
  const asked = "Name a tenant and list its credentials.";

  // Nothing listed yet, with an empty field and with a full one. The second is
  // the case the effect used to hide: the operator has typed a tenant name and
  // the console has not asked the deployment anything about it.
  assert.equal(listingNotice(initialCredentials(), ""), asked);
  assert.equal(listingNotice(initialCredentials(), "acme"), asked);

  // Asked, and the answer was genuinely nothing. The only state in which the
  // console may say so.
  const empty = withRecords(initialCredentials(), [], "acme");
  assert.equal(listingNotice(empty, "acme"), "This tenant holds no credentials.");

  // Rows in hand, describing the tenant named: they are shown, so there is
  // nothing to say in their place.
  const held = withRecords(initialCredentials(), [{ credential_id: "a" } as never], "acme");
  assert.equal(listingNotice(held, "acme"), null);

  // …and the moment the field stops naming that tenant, they stop describing
  // it. A half-typed name is not a shorter tenant.
  assert.equal(listingNotice(held, "acm"), asked);
  assert.equal(listingNotice(empty, "acm"), asked);
});

test("the tenant is compared trimmed, because that is how it was asked", () => {
  // `refresh` sends `listing.trim()`, so a field holding " acme " asked about
  // `acme` and the rows do describe what is named. Comparing the raw field
  // would blank the table on a trailing space.
  const listed = withRecords(initialCredentials(), [{ credential_id: "a" } as never], "acme");
  assert.equal(describes(listed, "  acme  "), true);
  assert.equal(listingNotice(listed, "  acme  "), null);
});

// -- revocation asks first ----------------------------------------------------
//
// It used to happen on the click. One control, one mis-aimed pointer, one key
// gone — and the warning FR-026a requires "before the first revocation" was
// rendered at the moment the request was already in flight.

test("asking about a revocation sends nothing and spends nothing", () => {
  const asked = askedToRevoke(initialCredentials(), "c1");

  assert.equal(asked.pending, "c1");
  // The warning is not spent by opening the confirmation — see the next test
  // for why that distinction is the point rather than a detail.
  assert.equal(needsWarning(asked), true);
});

test("backing out of a confirmation leaves the warning unspent", () => {
  // An operator who opened the dialog and thought better of it has performed no
  // revocation. Marking the warning shown here would mean the attempt they do
  // go through with is the first unwarned one — which is the requirement
  // inverted, not satisfied.
  const backedOut = revocationCancelled(askedToRevoke(initialCredentials(), "c1"));

  assert.equal(backedOut.pending, null);
  assert.equal(needsWarning(backedOut), true);
  assert.match(revocationPrompt(backedOut), /key you are using/);
});

test("confirming spends the warning, and only the first one carries it", () => {
  const first = askedToRevoke(initialCredentials(), "c1");
  assert.match(revocationPrompt(first), /key you are using/);

  const done = revocationConfirmed(first);
  assert.equal(done.pending, null);
  assert.equal(needsWarning(done), false);

  // An operator revoking five leaked keys does not need telling five times, and
  // a warning shown that often is one that is clicked through.
  const second = askedToRevoke(done, "c2");
  assert.doesNotMatch(revocationPrompt(second), /key you are using/);
});

test("every confirmation says the thing is irreversible, warned or not", () => {
  const first = askedToRevoke(initialCredentials(), "c1");
  const later = askedToRevoke(revocationConfirmed(first), "c2");

  for (const state of [first, later]) {
    assert.match(revocationPrompt(state), /cannot be undone/);
  }
});

test("one revocation is pending at a time", () => {
  // The model has no plural entry point, for the reason the erasure area has
  // none: a confirmation that names one target cannot be reused to act on two.
  const asked = askedToRevoke(askedToRevoke(initialCredentials(), "c1"), "c2");
  assert.equal(asked.pending, "c2");
});
