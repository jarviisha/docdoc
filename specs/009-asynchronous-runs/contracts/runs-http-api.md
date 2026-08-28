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
| `503` | run-state database unreachable; retryable (FR-057) |

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

The `200` for a running run means *requested*, not *stopped*. A provider call already in flight
completes and is billed (FR-029). The response body carries `status: "running"` until the boundary is
reached, and the documentation says why rather than letting a caller infer that the cancel failed.

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
