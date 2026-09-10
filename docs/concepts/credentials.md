# Credentials

Milestone 9 shipped authentication and left a defect in it, documented rather
than fixed: the key file was read **once, at startup**, so an operator who
deleted a compromised key saw no error, no warning, and no change. The revoked
key kept working until the process restarted.

This is the feature that closes it. Keys are issued, listed, and revoked at
runtime, and a revocation reaches every running process within a bound stated as
a number.

## The three properties

**The plaintext exists once.** `POST /v1/admin/credentials` returns the key in
its `201`, and nothing stores it — what goes in the table is `sha256(key)`. There
is no route that returns it again and none can be added without changing what
"stored as a digest" means.

**Revocation is an `UPDATE`, never a `DELETE`.** The row stays, the identifier is
never reused, and a revoked credential is never reissuable. "This key was revoked
on the 4th" is what an incident review needs, and a missing row cannot say it.

**Propagation is a bounded cache lifetime, and the bound is the documentation.**
Each process caches a resolution for `DOCDOC_RUN_CREDENTIAL_TTL_SECONDS`, default
**30 seconds**. Every process observes a revocation within one lifetime, with no
restart, no file, and no signal.

Thirty seconds, and not "immediately". "Immediately" is not a property anybody
can test, and a deployment that needs a shorter bound can set one — at the cost
of a database query per that many seconds per process.

## The asymmetry, and its direction

A cache **hit** is cached. A **miss is not**.

So issuance takes effect immediately, while revocation takes up to the lifetime.
That is safe in exactly one direction and the direction is stated rather than
discovered: caching misses would make a newly issued key fail for up to a
lifetime, which is the same defect this feature exists to remove, pointing the
other way.

## Two sources, and the order between them

The Milestone 9 **key file** is consulted first; the **table** second.

A deployment mid-migration keeps its file keys working while table-issued keys
start to, and no key silently stops working because a table appeared. A
deployment configured exactly as Milestone 9 configured it authenticates
identically and reaches no administrative surface at all.

The file ring cannot issue, revoke, or list — it raises a typed refusal naming
the missing database, which is the honest answer: a file cannot issue.

## Scopes

A principal carries a tenant and a set of scopes. There is one scope,
`admin`, and it is **a capability on a principal, never a second tenant**: every
credential still resolves to exactly one tenant.

Upgrading grants nobody anything. A principal built from the file ring has empty
scopes, so a Milestone 9 deployment gains no administrative access by installing
this version.

**The first administrative credential comes from the command line.** `docdoc
credential issue --admin` is the only way one comes into existence; `POST
/v1/admin/credentials` refuses `scopes: ["admin"]`, because a route that can mint
an administrative key is a route that needs no credential.

## Administrative routes answer `404`

A tenant credential presenting itself at an admin route gets `404`, not `403`. An
ordinary tenant learning that an administrative surface exists at a URL is a
disclosure with no upside.

`403` appears exactly once: `POST /v1/admin/credentials` refusing to mint an
admin scope. The caller is *already* an administrator, so there is nothing to
conceal from them, and the body has to explain where the first one comes from.

## One refusal for four causes

An absent credential, a malformed one, an unrecognised one, and a revoked one
produce **one indistinguishable response**. A refusal that said which would be an
oracle: it would tell an attacker whether a key they hold ever existed.

That is the opposite of a limit refusal, which names the limit and says when to
come back — see [limits](limits.md). Being over a quota is not a secret; holding
a key that used to work is.

## The lockout that is possible

Revoking the last administrative credential leaves a deployment with no way to
issue another **through the API**. That is possible, and it is documented rather
than prevented, because the prevention would be a route that mints an admin key
without one.

The recovery is the command line, against the database:

```bash
docdoc credential issue --admin --tenant ops
```

An operator with database access is an operator who can already do anything;
requiring that access is the point.

## What is never logged

Neither the key nor its digest. The `credential.operation` audit event names the
actor, the operation, the credential identifier, and the tenant — an audit trail
that recorded what it was auditing would be the single richest place to steal
from in the deployment.

`last_used_at` is best-effort and explicitly **not** in the request's
transaction: it is written on a cache miss, so it is accurate to within one cache
lifetime per process rather than to the request. Exactness would put a database
write on every authenticated request, which is the cost the cache exists to
avoid.

## Configuration

| Variable | Meaning | Default |
|---|---|---|
| `DOCDOC_API_KEYS_FILE` | the file ring; **its presence is what turns authentication on** | unset — authentication off |
| `DOCDOC_RUN_CREDENTIAL_TTL_SECONDS` | cache lifetime, and therefore the revocation bound | 30 |
| `DOCDOC_RUN_DATABASE_URL` | where credentials live; without it, none can be issued | unset |

Neither has a command-line flag. The key file names a credential, and `argv` is
readable by every process on the host; the TTL must be the same in every process
that authenticates, so it belongs to the deployment rather than to an invocation.

## See also

- [ADR-0016 — credential lifecycle](../adr/0016-credential-lifecycle.md)
- [ADR-0014 — tenant scoping and store namespacing](../adr/0014-tenant-scoping-and-store-namespacing.md)
- [limits](limits.md) — the other refusal, and why it says more
