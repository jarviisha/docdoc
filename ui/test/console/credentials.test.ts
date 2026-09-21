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
  describes,
  disclosureFrom,
  initialCredentials,
  isActive,
  listingNotice,
  needsWarning,
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
