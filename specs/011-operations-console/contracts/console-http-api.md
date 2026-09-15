# Contract: The Operations Console's HTTP Surface

**Feature**: `specs/011-operations-console/` | **Date**: 2026-09-11

One route is added. Everything else on this page is a route that already exists, listed because the
console consumes it **unchanged** — and because "unchanged" is a claim worth being able to check.

---

## Added — exactly one, and it is a read

### `GET /v1/runs` — this tenant's runs, newest first

```
GET /v1/runs?status=failed&limit=50&cursor=<opaque>
Authorization: Bearer <key>
```

| Parameter | Type | Default | Rules |
|-----------|------|---------|-------|
| `status` | one of `queued`, `running`, `succeeded`, `failed`, `cancelled` | none | any other value is a `422`. The set is closed and this route does not widen it (FR-017, FR-004) |
| `limit` | integer | `50` | maximum `200`; above it is a `422` rather than a silent clamp, because a silently clamped page is one a client pages through wrongly forever |
| `cursor` | opaque string | none | from a previous response's `next_cursor`. Malformed, or issued for another tenant: `400`, with the same body either way (FR-016) |

**200**:

```json
{
  "runs": [
    {
      "run_id": "…",
      "status": "failed",
      "created_at": "2026-09-11T09:12:04Z",
      "finished_at": "2026-09-11T09:12:51Z",
      "blob_id": "sha256:…",
      "schema": "invoice@1",
      "processing_id": null
    }
  ],
  "next_cursor": "…"
}
```

`next_cursor` is `null` on the last page. An empty `runs` array with a non-null cursor is not a state
this route produces.

**A page filled exactly to the limit still carries a cursor**, because it cannot know whether it is
the last — following it lands on an empty page, which ends. That costs one round trip on the boundary
case, against a `COUNT(*)` on *every* page to answer a question that matters once per listing. Worth
stating because the contract's first reader assumed the other behaviour and wrote a test for it.

**Scoping.** The tenant is a **predicate in the query**, not a filter after the fetch (ADR-0014 §3,
FR-014). Another tenant's runs are absent for the same reason a nonexistent run is absent, from the
same branch, so the two cannot drift into two behaviours.

**Ordering.** `created_at DESC, run_id DESC` — `run_id` is the primary key's name in
`0001_runs.sql`, and `schema` in the body above is projected from the `schema_identity` column.
Keyset, never `OFFSET`: an offset page shifts under
concurrent submission, which would make FR-018's "every run exactly once" false in production and
true in every test (research R2).

**Removed runs are absent** (FR-020). A tombstone is reachable by identity, by its owner, through
`GET /v1/runs/{run_id}` exactly as `specs/010` FR-011 defines. The listing is not a second way to
learn that something was deleted.

**No `403` for scope.** This route needs no administrative scope: a principal lists their own
tenant's runs. Listing across tenants does not exist in this milestone.

**Errors**: `400` malformed or foreign cursor · `422` unknown status or oversized limit ·
`401` no or rejected credential, when authentication is enabled · `503`-shaped "runs are not
available in this deployment" when no database is configured, matching what `GET /v1/runs/{run_id}`
already answers.

---

## Consumed unchanged

The console calls each of these and **must not** cause any of them to change.

| Route | Used for | Milestone |
|-------|----------|-----------|
| `GET /v1/runs/{run_id}` | run detail, including `routing` when a policy is configured, and the `410` tombstone body | 9 / 10 |
| `GET /v1/runs/{run_id}/delivery` | the delivery record: attempts, outcomes, at-rest state (User Story 5) | 10 |
| `GET /v1/jobs/{processing_id}/result` | **linked to, never rendered** (FR-022a) | 7 |
| `POST /v1/admin/credentials` | issuance; the key is in the response once and never again | 10 |
| `GET /v1/admin/credentials?tenant_id=…` | the credential listing, and the console's one probe for administrative scope (research R7). **`tenant_id` is required** — see below | 10 |
| `DELETE /v1/admin/credentials/{credential_id}` | revocation, idempotent | 10 |
| `DELETE /v1/admin/tenants/{tenant_id}` | tenant erasure | 10 |
| `DELETE /v1/admin/documents/{blob_id}` | document erasure | 10 |

**`404` and not `403`** on every administrative route for a non-administrative principal
(ADR-0016 §6). The console reads that `404` as "this area does not exist" and hides it (FR-028); it
does not translate it into a permission message, which would put back the disclosure the `404` exists
to remove.

### `tenant_id` is required on the credential listing, and it costs a design decision

The route answers **`422`** without it. This page said nothing about that until T080, and the
omission was expensive: the console's first administrative probe called the route bare, read the
`422` as "not an administrator", and hid the credentials and erasure areas **from administrators**.
Nothing caught it — the fake queue in the contract tests does not model this route's parameters, so
it took bringing up the composition (T072) to find.

It matters more than a missing parameter usually would, because **the console never learns its own
tenant**. No route reports one, and adding one would be the second new read SC-003 forbids. So the
probe asks about a tenant that holds nothing:

```
GET /v1/admin/credentials?tenant_id=docdoc-scope-probe
  → 200 {"credentials": []}   to a principal holding `admin`
  → 404                        to every other principal
```

The scope check runs **before** the lookup, which is what makes this work: a nonexistent tenant is a
valid question that discloses nobody's data, and the two answers are exactly the question the console
is asking. `PROBE_TENANT` in `ui/src/console/model/client.ts` is that constant.

The listing proper takes the tenant the operator names, because an operator issuing a credential
already knows which tenant they are issuing it for.

---

## Added — one mount, and it is not behind the credential

### `GET /console`, `GET /console/{path}` — the console's static assets

Served by the API process from the `docdoc-ui` distribution's `dist-console` tree, or from
`ui/dist-console` in a checkout, through the same three-candidate search
`docdoc.api.ui.locate_assets` already performs for the viewer.

**Unauthenticated, and this is the milestone's one deliberate divergence.** The viewer's mount
carries the router's credential dependency; `app.py:709` documents what that costs — with
authentication enabled the viewer *"does not load"*. A browser navigation cannot carry a bearer
token, and the console's premise is that a key is entered **after** the page loads (FR-006). Gating
the shell does not make the console strict; it makes it impossible.

What is served is HTML, CSS, and JavaScript. No run, no tenant identifier, no credential, and no
document is reachable from those bytes, and the console issues **zero** requests before a key is
entered. The full argument and the three rejected alternatives are in research R1 and the plan's
Complexity Tracking.

**Absent assets are a `501` naming what is missing**, exactly as the viewer's are — a blank page is
what `specs/008` FR-037 forbids, and the console inherits both the rule and the two different
sentences for a missing build and a missing distribution.

---

## What is deliberately absent

No route names `assign`, `assignment`, `reviewer`, `reviewers`, `queue`, `worklist`, `work-list`,
`inbox`, `task`, `tasks`, `approve`, `approval`, `reject`, or `escalate`.
`tests/contract/test_no_review_platform.py` asserts it, and FR-003 forbids this milestone from
modifying that test. A worker-backlog view, if one is ever built, is named `backlog` (ADR-0019 §3).

No route reports the caller's own credential identity or scope. No route reports whether
authentication is enabled. Both are answered by attempting a request and observing the answer
(research R6, R7), because each would otherwise be a second new read — and "exactly one new
server-side concept" is what SC-003 measures.

No route lists tenants, no route lists across tenants, no route reports limit counters, and no route
serves a stored document's bytes. The last one is why the console does not link into the viewer
(FR-022b); its cost is recorded in the spec's Out of Scope rather than discovered later.
