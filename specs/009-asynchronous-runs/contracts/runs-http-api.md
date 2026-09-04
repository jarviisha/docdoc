# Contract: Run Routes, Health Routes, and Authentication

**Feature**: `009-asynchronous-runs` | **Date**: 2026-08-28

What this milestone adds to the HTTP surface, and — as important — what it leaves untouched.

## Unchanged

These keep their route, request shape, response shape, and status codes. A caller written against
Milestone 8 observes no difference (FR-008, FR-009, SC-018).

```text
POST /v1/documents                        the blob identity
GET  /v1/documents/{blob_id}              size and media type
POST /v1/documents/{blob_id}/extract      synchronous; returns job id and result
POST /v1/extract                          synchronous, storeless — no async variant exists (FR-010)
GET  /v1/schemas                          configured identities
GET  /v1/jobs/{job_id}                    succeeded | unavailable | unknown — still no `pending`
GET  /v1/jobs/{job_id}/result             the stored result
```

`GET /v1/jobs/{job_id}` keeping its closed three-status set is the visible consequence of ADR-0013 §1.
A run in progress is not reported here, because `job_id` is `processing_id` and that identity does not
exist yet. It is reported under `run_id`, on a different resource.

## Added

### `POST /v1/documents/{blob_id}/runs`

Accepts a run and returns before any stage executes.

**Query**: `schema` — a concrete `name@version`, same vocabulary as the synchronous route.

**Headers**: `Idempotency-Key` (optional), `X-Request-Id` (optional, forwarded into
`pipeline.run(request_id=…)` unchanged).

**202 Accepted**

```json
{ "run_id": "0f8b…", "status": "queued", "created_at": "2026-08-28T09:14:22Z" }
```

No `processing_id` field — **absent, not null**. This follows ADR-0012 §3's precedent exactly: a null
would invite the caller to send it to `GET /v1/jobs/{id}`, which would answer `unknown` about an
identity nobody issued.

| Status | Condition |
|---|---|
| `202` | accepted |
| `200` | idempotency key already seen for this tenant; body is the original run (FR-011) |
| `401` | credential absent or unrecognised, when authentication is enabled |
| `404` | blob unknown **or** owned by another tenant — indistinguishable (FR-066) |
| `422` | schema identity not configured |
| `503` | run-state database **or** document store unreachable; retryable (FR-057) |

The store's half of that `503` is the newer one, and it used to be a `404`. Asking "is this document
here?" of an unreachable store got the same answer as asking it of a store that does not have the
document, so a caller was told their document was gone when the store was merely down. That is the
absent-versus-unreachable conflation this milestone removed from the blob stores, surfacing one layer
up — `blobs.size_of` now raises rather than returning `None`, and `api.errors.status_for` maps
`ArtifactError(reason="unavailable")` to `503`.

Mapped there rather than caught at the route, so that one table decides what a typed error means over
HTTP. A second place deciding it is how the two come to disagree.

`reason="not_configured"` deliberately stays `500`. A store nobody configured is a deployment fault,
not a transient one: `503` invites a retry, and retrying will not conjure a store.

### `GET /v1/runs/{run_id}`

```json
{
  "run_id": "0f8b…",
  "status": "succeeded",
  "attempts": 1,
  "created_at": "…", "updated_at": "…",
  "processing_id": "sha256:3a1e…",
  "stage_outcomes": [ {"stage": "parse", "status": "reused", "duration_ms": 41}, … ]
}
```

Failed runs carry `failed_stage` and `error_class` instead of `processing_id`:

```json
{ "run_id": "…", "status": "failed",
  "failed_stage": "extract", "error_class": "ProviderError",
  "stage_outcomes": [ {"stage": "parse", "status": "executed", "duration_ms": 812} ] }
```

A run whose schema identity stopped resolving between submission and claim carries no `failed_stage`
at all, because it reached no stage:

```json
{ "run_id": "…", "status": "failed",
  "failed_stage": null, "error_class": "SchemaError",
  "stage_outcomes": [] }
```

That run is terminal on its first claim and is never retried or reported as abandoned (FR-091) — a
withdrawn schema is a configuration fault, and naming it with the word for a poison document sends an
operator to the wrong place.

`error_class` is a class name and never a message. A message can quote the document it choked on —
the rule `PipelineResult` and `pipeline/observe.py` already follow, inherited here rather than
restated.

| Status | Condition |
|---|---|
| `200` | any of the five states, including terminal failures |
| `404` | unknown, or owned by another tenant — byte-identical responses (FR-066, SC-008) |

**The result is not served here.** A succeeded run names its `processing_id`, and the existing
`GET /v1/jobs/{processing_id}/result` returns it. One result representation, reachable one way
(FR-013).

### `DELETE /v1/runs/{run_id}`

Requests cancellation. Returns the run.

| Status | Condition |
|---|---|
| `200` | queued → cancelled immediately; running → cancellation requested, honoured at the next stage boundary |
| `200` | already `cancelled` — idempotent (FR-034) |
| `409` | already `succeeded` or `failed`, naming the state (FR-031) |
| `404` | unknown or another tenant's |

**The `200` for a running run means *requested*, not *stopped*** (FR-029). This is the one place
this contract is likely to be misread, so it is stated three times — here, in `docs/concepts/runs.md`,
and in the route's own docstring — rather than left to be inferred:

- The response body carries `status: "running"`, not `"cancelled"`. Reporting `cancelled` would be
  the one lie this endpoint must not tell: the work has not stopped yet.
- The worker observes the request at its **next stage boundary**. A stage already executing runs to
  completion, so a provider call in flight is completed *and billed*. Nothing is aborted — SC-015
  measures that at 0%.
- What stops is the stage that had not started. Cancelling before the extract stage is the case with
  an economic point, because that is where the model call is.
- Poll `GET /v1/runs/{run_id}` to observe the run reach `cancelled`. A cancelled run carries no
  `processing_id`, because no terminal artifact was produced, and it carries no `failed_stage`
  either: a cancellation is not a failure and no stage refused anything.

A **queued** run is different and total: it moves to `cancelled` immediately and is never claimed.

### `GET /healthz` and `GET /readyz`

Outside `/v1`, unauthenticated (FR-058), served by both process types on the same terms (FR-053,
FR-054).

`/healthz` returns `200 {"status":"alive"}` and touches nothing.

`/readyz` returns `200 {"status":"ready"}`, or `503` naming the unmet dependency:

```json
{ "status": "not_ready", "unmet": ["run-state-database"] }
```

Readiness is **strict** and covers every dependency: a process that cannot reach the database reports
not ready even though the synchronous routes would still work, which withdraws working capacity on
purpose (FR-087, and the third clarification of 2026-08-28). Neither route discloses configuration
values, credentials, tenant identifiers, or counts of stored content.

## Authentication

Disabled by default (FR-088). Enabled by pointing `DOCDOC_API_KEYS_FILE` at a key file; then every
route except `/healthz` and `/readyz` requires `Authorization: Bearer <key>`.

- **Every** route requires one, and the exemption list is exactly `/healthz` and `/readyz`. That
  includes the `/ui` mount and FastAPI's `/docs`, `/redoc` and `/openapi.json`, none of which is on
  the `/v1` router and all of which were open until convergence found them. The consequence is worth
  stating: a browser cannot send a bearer token, so the viewer is unavailable on an authenticated
  deployment — it fails at the door rather than after the page has rendered.
- On an authenticated deployment an unknown path answers `401`, not `404`. The credential is checked
  before routing, and a `404` would say which paths exist to somebody who cannot use any of them.
- A key resolves to a principal carrying exactly one `tenant_id` (FR-060).
- Rejection happens **before** any document is read, any provider is called, or any store is touched
  (FR-067).
- A credential never appears in a log line, a run record, an error body, or a process argument list
  (FR-068, R14).
- With authentication disabled the deployment has one implicit tenant owning everything, and behaves
  exactly as Milestone 8 did (FR-088).

**Cross-tenant responses are byte-identical to non-existence.** Not merely the same status: the same
body. SC-008 asserts it, and SC-017 asserts the half a status code cannot deliver — that the *cost and
timing* are identical too, because a content-addressed store shared across tenants would otherwise let
a submission prove another tenant holds a document.

## Error bodies

Unchanged in shape from Milestone 7 — `{"error": {"class": "…", "message": "…"}}` — with every new
error a typed, provider-neutral `RunError` subclass (FR-074). The `class` field is the docdoc error
name, never a driver's or a provider's.
