# Contract: Credential, Correction, Callback, and Erasure Routes

**Feature**: `010-operations-and-corrections` | **Date**: 2026-09-04

What this milestone adds to the HTTP surface, what it changes on two existing routes, and — as
important — what it leaves untouched. Every addition is inert until configured (FR-101).

## Unchanged

These keep their route, request shape, response shape, and status codes. A caller written against
Milestone 9 that enables nothing observes no difference (SC-002).

```text
POST /v1/documents                        the blob identity
GET  /v1/documents/{blob_id}              size and media type
POST /v1/documents/{blob_id}/extract      synchronous
POST /v1/extract                          synchronous, storeless
GET  /v1/schemas                          configured identities
GET  /v1/jobs/{job_id}                    succeeded | unavailable | unknown
GET  /v1/jobs/{job_id}/result             the stored result
GET  /healthz, GET /readyz                unauthenticated, disclose nothing
DELETE /v1/runs/{run_id}                  cancellation, unchanged
```

**`RunStatus` is unchanged and closed at five** — `queued`, `running`, `succeeded`, `failed`,
`cancelled` (FR-004a). A client switching exhaustively on it keeps being right, and SC-023 asserts
the set by name. Nothing in this milestone adds `expired`, `erased`, or `needs_review`.

## Changed

### `POST /v1/documents/{blob_id}/runs` — two optional inputs, one new refusal

**Body** (all fields optional; an empty body is the Milestone 9 request):

```json
{ "priority": "urgent", "callback_id": "3c1a…" }
```

- **`priority`** — `ordinary` or `urgent`, defaulting to `ordinary` (FR-087). A request above the
  tenant's ceiling is **accepted at the ceiling** and the granted value is stated in the response;
  it is not refused, because an operator lowering a ceiling must not break a client that changed
  nothing (FR-087a).
- **`callback_id`** — a destination registered under this tenant. Unknown or another tenant's
  callback is `404`, indistinguishable from one that never existed.

**202 Accepted**

```json
{ "run_id": "0f8b…", "status": "queued", "priority": "ordinary",
  "created_at": "2026-09-04T09:14:22Z" }
```

`priority` is echoed **always**, including when it was not requested, so a client can see what it was
granted without comparing against a ceiling it cannot read.

| New status | Condition |
|---|---|
| `429` | a configured limit was reached: submission rate, concurrent runs, period count, or token budget (FR-043) |

The `429` body names the limit and carries `Retry-After`:

```json
{ "error": "limit_exceeded", "limit": "concurrent_runs",
  "observed": 25, "allowed": 25, "retry_after_seconds": 30 }
```

**It is distinct from `401` and `403` in kind and not only in code** (FR-043): a refusal for being
over a limit says which limit and when to return; a refusal for credentials says nothing at all,
because saying more is what an attacker is asking for.

**No run is created, no queue position consumed, and no store touched** (FR-044). A `429` costs the
deployment one counter read.

### `GET /v1/runs/{run_id}` — two additions and one new outcome

The success body gains two fields, both absent rather than null when they do not apply:

```json
{ "run_id": "0f8b…", "status": "succeeded", "priority": "ordinary",
  "processing_id": "9ab2…",
  "routing": { "outcome": "review", "policy_version": "default@3",
               "reasons": [ {"field": "total", "signal": "grounding",
                             "observed": "ungrounded"} ] } }
```

`routing` is absent when no policy is configured (FR-075), and its `outcome` is one of exactly two
values (FR-067). It is never a score.

**`410 Gone` — the run existed and does not** (FR-011):

```json
{ "error": "run_erased", "run_id": "0f8b…",
  "deleted_at": "2026-09-04T00:00:00Z", "policy": "retention" }
```

| Status | Condition |
|---|---|
| `200` | the run, owned by this tenant |
| `410` | a tombstone exists for this tenant — it was here and is gone |
| `404` | no run and no tombstone, **or** either belonging to another tenant |

**`410` and `404` differ in kind, which is the point.** A tenant's own erased run is knowable to that
tenant; to anyone else it is byte-identical to an identifier that never existed (FR-011, preserving
Milestone 9's FR-066). SC-005 measures both halves.

## Added — credentials

All four require the `admin` scope (FR-031). A tenant credential presenting itself here gets `404`,
not `403`: an ordinary tenant learning that an administrative surface exists at a URL is a disclosure
with no upside.

### `POST /v1/admin/credentials`

```json
{ "tenant_id": "acme", "scopes": [], "label": "ci-runner" }
```

**201 Created** — and this is the **only** time the key is ever readable (FR-025):

```json
{ "credential_id": "7f2c…", "tenant_id": "acme",
  "key": "ddk_9Xh…", "created_at": "2026-09-04T09:00:00Z" }
```

The response says so in its own documentation and the CLI prints a warning. There is no route that
returns it again, and none that can be added without changing what "stored as a digest" means.

`scopes: ["admin"]` is **refused here** (FR-032). The first administrative credential is created by
`docdoc credential issue --admin` against the database; a route that can mint an admin key is a route
that needs no credential.

### `GET /v1/admin/credentials?tenant_id=acme`

```json
{ "credentials": [
  { "credential_id": "7f2c…", "tenant_id": "acme", "label": "ci-runner",
    "scopes": [], "created_at": "…", "last_used_at": "…", "revoked_at": null } ] }
```

No `key`, no `digest`, and no field from which either can be reconstructed (FR-030).

### `DELETE /v1/admin/credentials/{credential_id}`

**204**, idempotent. Revoking a revoked credential is `204` and changes nothing. Effective within the
credential cache lifetime on every process (FR-027, FR-028) — the bound is `DOCDOC_CREDENTIAL_TTL`,
default 30 s, and the operator documentation states it as a number rather than as "immediately".

**A revoked credential is never reissuable and its identifier is never reused** (FR-036).

## Added — callbacks

### `POST /v1/callbacks`

```json
{ "url": "https://hooks.example.com/docdoc", "secret": "whsec_…" }
```

**201** returns `callback_id` and never the secret again (FR-066).

| Status | Condition |
|---|---|
| `201` | registered |
| `422` | destination refused by policy — non-HTTPS, or resolving to loopback, link-local, private, multicast, reserved, or unspecified (FR-060) |

The `422` names the class of refusal and **not the addresses it resolved to**, which would make the
route a resolver for an unauthenticated caller.

### `DELETE /v1/callbacks/{callback_id}` — `204`, idempotent.

### `GET /v1/runs/{run_id}/delivery`

```json
{ "delivery_id": "b41e…", "state": "failed", "attempts": 6,
  "last_status": 503, "last_error": "HTTPError",
  "next_attempt_at": null }
```

`last_error` is a **class name**, never a receiver's response body (the rule `error_class` follows on
runs). `404` when no callback was registered — an absent delivery is not an error.

## Added — corrections

### `POST /v1/runs/{run_id}/corrections`

Body is the `Correction` model of `docdoc.evaluation.corrections`, unchanged and not redefined
(FR-077). `annotator` is supplied by the caller and **is never inferred from the credential**
(FR-085) — the person who reviewed a value and the key that submitted it are different facts.

| Status | Condition |
|---|---|
| `201` | recorded; the result, its artifacts, and its identities are unchanged (FR-078) |
| `404` | run unknown, or another tenant's |
| `410` | the run was erased — naming which, because an annotation against something gone is uninterpretable (FR-082) |
| `409` | the run has no result to correct (not succeeded) |

### `GET /v1/runs/{run_id}/corrections` — the tenant's own, and nobody else's (FR-079).

### What is deliberately absent

No assignment route, no reviewer queue, no work list, no review state, no `PATCH` on a result, and no
write path in the viewer (FR-083, FR-084). Principle IX permits the model and forbids the platform,
and the deferred-technology list names "a full review UI". The absence is the contract.

## Added — erasure

### `DELETE /v1/admin/tenants/{tenant_id}` and `DELETE /v1/admin/documents/{blob_id}`

Both require `admin`. Both are idempotent and both succeed having removed nothing when there is
nothing to remove (FR-009).

**200 OK**, synchronous, reporting what went:

```json
{ "scope": "tenant", "target": "acme",
  "runs": 128, "artifacts": 512, "blobs": 128, "rows": 4, "degraded": false }
```

**Corrected 2026-09-04.** This said `202`, and that the maintenance tick would do the work. The tick
sweeps and has never had an erasure to perform, so the route answered "accepted" and did nothing —
the failure spec.md names by hand: *an answer that is worse than refusing, because it is believed.*

The `202` design needed a request table, a migration, a tick step, and a status route to be useful,
for an operation an operator invokes rarely and waits for. `docdoc erase` already does the work
synchronously; two paths doing one thing the same way beat one path doing it through a queue nothing
else uses. The bound is stated rather than implied: at most 10 000 runs per call, and a larger tenant
goes through the command line.

**The default tenant is refused here, always** (R2, FR-006). Its namespace is the store root
(ADR-0014 §3), so a prefix erasure of it would remove every other tenant's content and everything the
command line ever wrote. Erasing the default tenant's *run-derived* content is available through
`docdoc erase --tenant default`, which uses the set-difference path; emptying the root outright needs
an explicit destructive flag on the command line and exists at no URL.

```json
{ "error": "default_tenant_erasure_refused",
  "detail": "the default tenant's namespace is the store root; use the CLI" }
```

## Status code summary for what is new

| Code | Meaning here |
|---|---|
| `410` | this identifier was yours and has been erased (FR-011) |
| `429` | a configured limit was reached; `Retry-After` says when (FR-043) |
| `422` | a callback destination the policy refuses (FR-060) |
| `404` | unknown, another tenant's, or an admin surface seen by a non-admin |

**`403` appears once, and `404` is still the answer to every authorisation failure.** The exception is
`POST /v1/admin/credentials` refusing `scopes: ["admin"]`: the caller is *already* an administrator,
so there is nothing to conceal from them, and the body has to explain that the first administrative
credential comes from the command line (FR-032). Concealment is for callers who should not know a
surface exists; this one already does.

`409` appears twice, for the same reason a `409` usually does — "not in this state". Erasing the
default tenant is refused with one, and so is a credential operation on a source that cannot perform
it. Both are refusals to do something, not refusals to admit something exists.

Corrected 2026-09-04: this section previously said `403` appeared nowhere and omitted `409`
altogether.
