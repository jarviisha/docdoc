# Rotating a credential

Issue the replacement, let both work for a while, then revoke the old one. That
overlap is the whole technique: rotating in the other order means a window in
which the client has no working key.

## Before you start

Credentials live in the run-state database, so the deployment needs one:

```bash
export DOCDOC_RUN_DATABASE_URL='postgresql://docdoc:docdoc@localhost:5432/docdoc'
docdoc migrate
```

## The first administrative credential comes from the command line

There is no route that can mint one — a route that could would be a route that
needs no credential:

```bash
docdoc credential issue --admin --tenant ops --label bootstrap
```

```json
{"credential_id": "7f2c…", "tenant_id": "ops",
 "key": "ddk_9Xh…", "created_at": "2026-09-09T09:00:00Z"}
```

**That is the only time the key is readable.** The table holds `sha256(key)` and
nothing else. There is no route that returns it again, and none can be added
without changing what "stored as a digest" means. Copy it into your secret
manager now.

```bash
export ADMIN_KEY='ddk_9Xh…'
```

## Rotate, in the order that has no gap

**1. Issue the replacement.**

```bash
curl -X POST http://localhost:8000/v1/admin/credentials \
  -H "authorization: Bearer $ADMIN_KEY" \
  -H 'content-type: application/json' \
  -d '{"tenant_id": "acme", "label": "ci-runner-2026-09"}'
```

```json
{"credential_id": "a4d1…", "tenant_id": "acme", "key": "ddk_Kp2…",
 "created_at": "2026-09-09T09:05:00Z"}
```

**Issuance takes effect immediately.** Cache misses are not cached, so the new
key works on the next request against every process.

**2. Deploy the new key to the client.** Both keys work. Two active credentials
for one tenant is a supported state, not a problem to hurry through — the overlap
is what makes the rotation gapless.

**3. Confirm the old one has stopped being used.**

```bash
curl 'http://localhost:8000/v1/admin/credentials?tenant_id=acme' \
  -H "authorization: Bearer $ADMIN_KEY"
```

```json
{"credentials": [
  {"credential_id": "7f2c…", "label": "ci-runner", "scopes": [],
   "created_at": "…", "last_used_at": "2026-09-09T09:04:12Z", "revoked_at": null},
  {"credential_id": "a4d1…", "label": "ci-runner-2026-09", "scopes": [],
   "created_at": "…", "last_used_at": "2026-09-09T09:31:40Z", "revoked_at": null}]}
```

No key and no digest in the listing, and no field either can be reconstructed
from. A listing that could produce what it describes would be a worse leak than
the file it replaced.

`last_used_at` is **accurate to within one cache lifetime per process**, not to
the request. It is written on a cache miss, because a write on every
authenticated request is exactly the cost the cache exists to avoid. For "is this
key still in use before I revoke it", 30-second granularity answers perfectly
well.

**4. Revoke the old one.**

```bash
curl -X DELETE http://localhost:8000/v1/admin/credentials/7f2c… \
  -H "authorization: Bearer $ADMIN_KEY"
```

`204`, and idempotent — revoking a revoked credential is also `204` and changes
nothing. An operator reacting to a leak runs this twice, and the second attempt
failing would tell them something had gone wrong when nothing had.

## How long revocation takes

**Up to `DOCDOC_RUN_CREDENTIAL_TTL_SECONDS`, default 30 seconds**, on every
process, with no restart.

That is a number rather than the word "immediately", because "immediately" is not
a property anybody can test. Each process caches a resolution for that long; the
process that served the revocation forgets its cache at once, and the others
catch up within one lifetime.

Shorten it if you need a tighter bound:

```bash
export DOCDOC_RUN_CREDENTIAL_TTL_SECONDS=5
```

The cost is one database query per five seconds per process.

## Revoking under incident

```bash
docdoc credential revoke a4d1…
```

Works against the database with no API process involved, which is what you want
when the thing that leaked might be the API's own configuration.

A revoked credential is **never reissuable** and its identifier is never reused.
The row stays with a `revoked_at`, because "this key was revoked on the 9th" is
what an incident review needs and a missing row cannot say it.

## The lockout that is possible

Revoking your last administrative credential leaves no way to issue another
*through the API*. That is documented rather than prevented, because the
prevention would be a route that mints an admin key without one.

The recovery is the command line, against the database:

```bash
docdoc credential issue --admin --tenant ops --label recovery
```

An operator with database access can already do anything; requiring that access
is the point.

## What a refusal looks like

```json
{"error": {"class": "AuthenticationError", "message": "unauthenticated"}}
```

The same body for an absent credential, a malformed one, an unrecognised one, and
a revoked one. A refusal that said which would tell an attacker whether a key
they hold ever existed.

## See also

- [credentials](../docs/concepts/credentials.md)
- [ADR-0016](../docs/adr/0016-credential-lifecycle.md)
