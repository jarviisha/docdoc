/**
 * T102 (ADR-0023 §2) — the key is shown once, the listing never carries one,
 * and issuing a credential sends no scope at all.
 *
 * `scope` used to be something the operator chose (an admin-scoped key stays
 * CLI-only). ADR-0023 §2 drops the choice along with the field: a console
 * session issues a plain tenant credential, and the body sent for it is
 * `{tenant_id, label?}` with no `scopes` key at all — the server refuses any
 * request that carries one, even an empty list.
 */

import assert from "node:assert/strict";
import { test } from "node:test";

import {
  DISCLOSURE_WARNING,
  SELF_REVOCATION_WARNING,
  acknowledged,
  disclosureFrom,
  initialCredentials,
  isActive,
  issueBody,
  listedFor,
  listFailedFor,
  listingNote,
  needsWarning,
  scopesOf,
  showing,
  toRecords,
  warned,
  withDisclosure,
} from "../../src/console/model/credentials.ts";

test("issuing a credential sends no scope", () => {
  assert.deepEqual(JSON.parse(issueBody("acme", "")), { tenant_id: "acme" });
  assert.deepEqual(JSON.parse(issueBody("acme", "ci")), { tenant_id: "acme", label: "ci" });
});

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
        scopes: [],
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

test("the revocation warning is general and shown once per session", () => {
  assert.doesNotMatch(SELF_REVOCATION_WARNING, /this credential|that row|your key is/i);
  assert.match(SELF_REVOCATION_WARNING, /immediately/);

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
  const shown = showing(initialCredentials(), "acme");
  const one = listedFor(shown, "acme", [{ credential_id: "a" } as never]);
  const two = listedFor(one, "acme", [{ credential_id: "b" } as never]);
  assert.equal(two.records.length, 1);
});

test("before any Show, the note asks for a tenant", () => {
  assert.equal(listingNote(initialCredentials()), "Name a tenant and choose Show to list its credentials.");
});

test("an empty answer says the tenant holds none", () => {
  const state = listedFor(showing(initialCredentials(), "acme"), "acme", []);
  assert.equal(listingNote(state), "This tenant holds no credentials.");
});

test("a failed listing never claims the tenant holds none", () => {
  const state = listFailedFor(showing(initialCredentials(), "acme"), "acme");
  assert.equal(listingNote(state), "The credentials for this tenant could not be listed.");
});

test("while a listing is on its way, it does not claim the tenant holds none", () => {
  assert.equal(listingNote(showing(initialCredentials(), "acme")), "Listing credentials…");
});

test("a late answer for a tenant no longer shown is ignored", () => {
  const first = showing(initialCredentials(), "acme");
  const second = showing(first, "globex");
  const late = listedFor(second, "acme", [{ credential_id: "a" } as never]);
  assert.deepEqual(late, second);
  assert.deepEqual(listFailedFor(second, "acme"), second);
  const current = listedFor(second, "globex", [{ credential_id: "g" } as never]);
  assert.equal(current.records.length, 1);
  assert.equal(listingNote(current), null);
});
