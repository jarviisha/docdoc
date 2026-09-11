# ADR-0016: Credential Lifecycle, and the Requirement It Supersedes

- **Status**: Accepted
- **Date**: 2026-09-04
- **Implements**: Milestone 10 (`specs/010-operations-and-corrections/spec.md`), FR-025 – FR-038, FR-104
- **Relates to**: [ADR-0014](0014-tenant-scoping-and-store-namespacing.md) (authentication, tenant scoping, and the default-off decision this does not change)
- **Supersedes**: `specs/009-asynchronous-runs/spec.md` FR-061
- **Principles engaged**: XI (MVP discipline), and MVP Scope Constraints §Security

## Context

Milestone 9 shipped authentication and left a defect in it, documented rather than fixed.

`src/docdoc/api/auth.py` reads a key file **once, at startup**. An operator who deletes a compromised
key from that file sees no error, no warning, and no change: **the revoked key keeps working until
the process restarts.** Milestone 9's task T117 put that sentence in the operator documentation
precisely because it was too important to leave in a docstring, and its spec recorded the limitation
as an assumption — "Static credentials are sufficient for this milestone. Issuance, rotation, and
runtime revocation are Milestone 10."

This is that milestone. The requirement being superseded said so in its own text.

> **FR-061** (`specs/009`): The credential-to-principal mapping MUST be loaded from deployment
> configuration at startup, and MUST NOT be creatable or mutable through any route **in this
> milestone**.

`auth.py`'s module docstring states the consequence that follows from it: *"That is why it is a file
and not a table: a table invites exactly the endpoint the requirement forbids."* The endpoint is now
wanted, so the reasoning inverts, and this ADR records the inversion rather than leaving a merged
milestone's requirement quietly contradicted by later code.

Two things must survive the change. A deployment configured exactly as Milestone 9 configured it must
keep working, unchanged and unaware. And whatever replaces "restart to revoke" must state its own
bound as a number, because "immediately" is not a property anyone can test.

## Decision

### 1. Credentials live in a table, beside the runs

`credentials(credential_id, tenant_id, digest, scopes, label, created_at, last_used_at, revoked_at)`
in the existing run-state database. No second database, and no second migration mechanism — the
schema is applied by `docdoc migrate`, which Milestone 9 built to take more files.

`digest` is `sha256(key)`, the same derivation `auth.py:digest_of` already uses, so the file ring and
the table agree on what a stored credential is. **The plaintext is returned once, at issuance, and is
never stored and never retrievable again.** There is no route that returns it a second time, and none
can be added without changing what "stored as a digest" means.

`digest` is unique **deployment-wide**, not per tenant. A digest lookup must resolve to exactly one
principal, and it happens before the caller has been authenticated to name a tenant.

### 2. Revocation is an `UPDATE`, never a `DELETE`

Revoking sets `revoked_at`. The row stays, the identifier is never reused, and a revoked credential is
never reissuable. "This key was revoked on the 4th" is the answer an incident review needs, and a
missing row cannot give it.

### 3. Propagation is a bounded cache lifetime, and the bound is the documentation

Every process that authenticates holds a cache of digest → principal with a **configured lifetime,
defaulting to 30 seconds**. A revocation is a row update; every process observes it within one such
lifetime, with no restart, no file, and no signal. **FR-028's propagation bound *is* that lifetime**,
and the operator documentation states it as a number.

(The setting's name is deliberately not written here. This ADR is accepted before the code exists —
FR-104 requires that — and `tests/unit/test_documented_api_references_resolve.py` holds documentation
to naming only settings that resolve. The name lands with the implementation, in
`docs/concepts/credentials.md`, where an operator will look for it.)

**A miss is not cached.** Issuance therefore takes effect immediately, while revocation takes up to
the TTL. The asymmetry is safe in exactly one direction and the direction is stated rather than
discovered.

Two alternatives were considered:

- **A query per request** makes every route depend on database availability, including routes that
  need no database today. That is a much larger regression than the one this milestone already
  records (FR-113), and it trades a documented bound for an undocumented dependency.
- **`LISTEN/NOTIFY`** gives near-instant propagation and needs a dedicated connection per process held
  open forever, a reconnect path, and a fallback for the window in which it was down — which is a
  TTL, arrived at the long way and with more moving parts.

### 4. An administrative scope on the principal, not a role system

`Principal` gains `scopes: frozenset[str]`, defaulting to empty. One scope exists: `admin`.

It is a **capability on a credential**, not a second tenant and not a role. Milestone 9's FR-060 —
one credential resolves to exactly one tenant — is preserved unchanged. `TENANT_PATTERN` would
happily accept a customer named `admin`, which is why capability and identity are kept in different
fields.

A set rather than a boolean, so a second scope is not a migration; not a role table, because nothing
here asks for one.

### 5. The first administrative credential is a command-line act

`POST /v1/admin/credentials` refuses to issue `scopes: ["admin"]`. The first one is created by
`docdoc credential issue --admin`, acting on the database directly.

A route that can mint an administrative key is a route that needs no credential, which is the hole
the whole feature exists to close. The consequence — an operator can revoke every admin credential
and lock themselves out of the routes — is real, is recoverable from the command line, and is
documented. The alternative is an unrevokable key, which is worse.

### 6. Administrative routes answer `404` to a non-admin, never `403`

An ordinary tenant credential presenting itself at an admin route gets the response it would get for
a URL that does not exist. A `403` tells an attacker that an administrative surface is there and that
their key is the only thing missing; that is a disclosure with no upside, and it is the same reasoning
`AuthenticationError` already follows in refusing to distinguish absent from malformed from
unrecognised.

### 7. The file-backed key ring keeps working, and is consulted first

`KeyRing` implements `resolve` and raises `CredentialError` for issuance, revocation, and listing. A
deployment configured as Milestone 9 configured it authenticates exactly as before and reaches no
administrative surface, because principals from the file carry no scopes.

**Precedence: file first, table second.** A deployment migrating keeps its file keys working while
table-issued keys begin to work, and no key silently stops working because a table appeared.

## Consequences

**The Milestone 9 defect is closed, and closing it costs a database.** Runtime revocation needs
durable, shared state, so a deployment that serves only the synchronous routes but wants this now
needs Postgres where before it needed none. That is recorded in the spec as FR-113 and it ends
Milestone 9's assumption that "the database is a dependency of asynchrony only".

**Revocation is bounded, not instant, and that is stated everywhere it matters.** Thirty seconds is
short enough that "revoke and it stops working" is true in the sense an operator means, and the
number is configurable for a deployment that wants five.

**`last_used_at` is best-effort and explicitly not transactional** with the request it describes.
Making it exact would put a write on every authenticated request; it exists for the listing, and the
listing says so.

**A credential never appears anywhere else.** Not in a log line, a run record, an error body, a
telemetry attribute, a webhook payload, or a process argument list — the property `Principal` already
protects by carrying no key, extended to the four surfaces this milestone adds.

**Nothing here is a session mechanism.** The browser viewer still does not work under authentication,
for the same reason it did not in Milestone 8: a browser has no way to send a bearer token. What this
ADR buys is that an operator interface *becomes possible*; it does not build one, and "a full review
UI" remains on the deferred-technology list.
