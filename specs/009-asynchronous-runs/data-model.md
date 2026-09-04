# Data Model: Asynchronous Runs

**Feature**: `009-asynchronous-runs` | **Date**: 2026-08-28

One new entity. It is the first mutable persisted thing in the project, and everything below follows
from that one fact.

## The Run

An **attempt** to execute the pipeline over one document against one schema. Distinct from the
**result** the attempt produces, which is content-addressed, immutable, and lives in the artifact
store under `processing_id` (ADR-0013 §1).

| Field | Type | Null | Set by | Notes |
|---|---|---|---|---|
| `run_id` | uuid | no | `runs.identity` at submission | Opaque. Never derived from content (FR-002) |
| `tenant_id` | text | no | auth boundary | Constrained to `[a-z0-9_-]{1,64}`; validated before it reaches a store path (R12) |
| `blob_id` | text | no | submission | The source document's identity, unchanged from ADR-0002 |
| `schema_identity` | text | no | submission | Stored opaquely; no code branches on its value (FR-014, gate 7) |
| `status` | text | no | state machine | Closed set of five; see below |
| `attempts` | int | no | claim | Incremented in the claim statement itself (R8) |
| `worker_id` | text | yes | claim | Diagnostic only; nothing routes on it |
| `lease_until` | timestamptz | yes | claim, heartbeat | Null in every terminal state |
| `processing_id` | text | yes | completion | Present **only** when `status = 'succeeded'` (FR-004) |
| `failed_stage` | text | yes | completion | Copied from `PipelineResult.failed_stage` |
| `error_class` | text | yes | completion | A class **name**, never a message (FR-035, FR-037) |
| `stage_outcomes` | jsonb | yes | completion | Projection of `PipelineResult.outcomes`; see below |
| `cancel_requested` | boolean | no | cancel | Cancellation of a *running* run is a request, not a transition (FR-028, FR-029) — `status` stays `running`, so the request needs its own column. Added 2026-08-28, when implementing `is_cancelled` against a real table showed the model had described the behaviour without the field that carries it |
| `request_id` | text | yes | submission | The caller's correlation id, forwarded into `pipeline.run()` unchanged |
| `idempotency_key` | text | yes | submission | Unique per tenant when present (R15) |
| `created_at` | timestamptz | no | `runs.identity` | |
| `updated_at` | timestamptz | no | every transition | |
| `expires_at` | timestamptz | no | `runs.identity` | Retention deadline (FR-015) |

### `stage_outcomes` is a projection, not a second model

Per R2, `PipelineResult` already carries everything about what happened. The stored shape is a
narrowing of `StageOutcome` to the four fields that survive the no-content rule:

```json
[{"stage": "parse", "status": "reused", "artifact_id": "sha256:…", "duration_ms": 41},
 {"stage": "extract", "status": "failed", "artifact_id": null, "duration_ms": 2210}]
```

No value, no claimed text, no message. `failure_class` arrives already reduced to a class name by the
pipeline, so FR-037 is inherited rather than re-enforced here — which is the point of copying instead
of translating.

## States

```text
                     ┌──────────── cancel ────────────┐
                     ▼                                │
  submit ──────► queued ──── claim ────► running ─────┴──► cancelled
                     │                     │
                     │                     ├──► succeeded   (+ processing_id)
                     │                     │
                     │                     ├──► failed      (+ failed_stage, error_class)
                     │                     │
                     │                     └──► queued      (lease expired, attempts < limit)
                     │                              │
                     │                              └──► failed/abandoned  (attempts = limit)
```

Five states, and the set is closed (FR-006).

| State | Terminal | `processing_id` | `lease_until` |
|---|---|---|---|
| `queued` | no | null | null |
| `running` | no | null | set |
| `succeeded` | yes | **set** | null |
| `failed` | yes | null | null |
| `cancelled` | yes | null | null |

### Transition rules

1. **`queued → running`** happens only through the claim statement of R8, which is one statement, so
   no window exists between selecting a candidate and owning it.
2. **`running → queued`** on lease expiry, expressed as eligibility in the claim query rather than
   performed by a reaper. A worker shutting down cleanly performs it explicitly, so a rolling restart
   costs no lease timeout (FR-043).
3. **`running → failed/abandoned`** when `attempts` has reached the limit. Distinguishable from an
   ordinary failure by `error_class = 'RunAbandonedError'` (FR-038).
4. **`running → failed` on an unresolvable schema** is terminal at the first occurrence, never
   re-queued and never retried (FR-091). `attempts` shows the one claim that happened — the claim
   statement increments it atomically and nothing compensates — but the run is terminal, so the retry
   budget is not spent and `RunAbandonedError` is never reached. A configuration fault must not be recorded
   under the word for a poison document.
5. **Cancellation** applies to `queued` immediately and to `running` at the next stage boundary
   (FR-028). It is refused in every terminal state, naming the state (FR-031), and is idempotent in
   `cancelled` (FR-034).
6. **There is no `expired` state and no sweep.** `expires_at` is written at creation and read by
   nothing (FR-015). An earlier draft of this document described a retention sweep as though it
   existed, indexed for it, and listed a sixth state that no code path could ever set —
   `/speckit-analyze` raised it as CRITICAL. Retention is Milestone 10's, and it inherits a complete
   history because FR-007 forbids this milestone from deleting or re-transitioning anything.
7. **No transition out of a terminal state exists.** A succeeded run whose result is later cleared
   from the store stays `succeeded`; the store answers `unavailable` for the result, which is the
   existing job-route behaviour and not a run-state question.

### Invariants

- `processing_id IS NOT NULL` **iff** `status = 'succeeded'`. Enforced by a check constraint, because
  it is the one place the two identities of ADR-0013 §1 could be conflated by a careless update.
- `lease_until IS NULL` in every terminal state.
- `attempts >= 1` in every state except `queued`-before-first-claim.
- `failed_stage IS NULL` **and** `error_class` is a schema error, for a run that failed because its
  schema identity no longer resolved (FR-091) — the one terminal failure that reached no stage.
- A row is never deleted by any code path in this milestone. Milestone 10 owns deletion.

## Indexes

| Index | Purpose |
|---|---|
| `(run_id)` primary key | point lookup by the API |
| `(created_at)` partial `WHERE status IN ('queued','running')` | the claim query's candidate scan (R8); partial so terminal rows, which dominate over time, are not indexed |
| `(tenant_id, idempotency_key)` unique partial `WHERE idempotency_key IS NOT NULL` | FR-011 and SC-016 (R15) |
| `(tenant_id, created_at)` | tenant-scoped listing |

**`status` is in the predicate and not in the key**, and this row said otherwise until the fourth
convergence pass compared it against `0001_runs.sql`. Putting `status` first would be redundant — the
partial predicate already pins it to two values — and it would be actively worse: a composite
`(status, created_at)` cannot produce globally ordered `created_at` across both values, so the claim
query's `ORDER BY created_at` would need a sort the one-column index avoids. The index the database
has is the right one; the description was wrong, and
`tests/unit/test_data_model_matches_the_code.py` now compares the two so it cannot drift again.

No index on `expires_at`. Nothing queries it in this milestone, and an index with no reader is write
amplification on every insert; Milestone 10 adds it alongside the sweep that needs it.

## What is deliberately absent

- **No results table.** A result is an artifact, addressed by `processing_id`. Storing it here would
  make the database a second place a run's outcome lives, and ADR-0013 §2 refuses that in both
  directions.
- **No tenants table.** A tenant is what a credential resolves to (R14); it has no attributes this
  milestone reads. A table would be an entity created to hold a foreign key. `docdoc_default_tenant`
  below is not one: it holds a single row of *deployment configuration* — which tenant owns the
  unprefixed store root — and no per-tenant entity, no row per tenant, and no key anything references.
- **No retention sweep and no `expired` state.** See transition rule 6.
- **No priority, no queue name, no scheduled-for column.** Out of Scope, and each would be a column
  nothing reads.
- **No worker table.** `worker_id` is a diagnostic string. Worker liveness is the lease, and a
  registry of workers would be a second source of truth about which are alive.

## The other two tables

The milestone creates three tables and only one of them is the run. Both of these are described here
because a data model that documents one table of three sends a reader to `\dt` to find out what else
is there, and what they find has no explanation attached.

### `docdoc_default_tenant`

| Field | Type | Null | Set by | Notes |
|---|---|---|---|---|
| `singleton` | boolean | no | the migration's default | Primary key, `CHECK (singleton)`. One row, enforced — a second row would be a second answer to a question that has one |
| `tenant_id` | text | no | `docdoc migrate` | Who owns content written before tenants existed (FR-089). `CHECK` against `[a-z0-9_-]{1,64}`, restated here because this row is written by a command rather than by an authenticated request, and the auth boundary is the only other place it is validated |
| `assigned_at` | timestamptz | no | `docdoc migrate` | Passed in, never `now()` in SQL (FR-072) |

**Written by a command, not by a migration, and that is the point.** SQL cannot read an environment,
so a value hard-coded in `0002_default_tenant.sql` would be the inferred owner FR-089 forbids.
`docdoc migrate` reads `DOCDOC_DEFAULT_TENANT` and records it.

**It refuses to change once recorded**, and that refusal is load-bearing rather than defensive. The
value decides where every read looks, so moving it after content exists strands everything written
under the old tenant — and the symptom is not an error but *correct answers plus a silent re-payment
for every parse*, because a miss is indistinguishable from an absence. `assign_default_tenant` raises
`TenantAssignmentError` naming both tenants, which turns a misconfigured second deployment into a
failure at deploy time instead of a discovery on next month's invoice.

### `docdoc_schema_version`

| Field | Type | Null | Set by | Notes |
|---|---|---|---|---|
| `version` | text | no | the migration runner | Primary key. The `.sql` file's stem, e.g. `0001_runs` |
| `applied_at` | timestamptz | no | the migration runner | Passed in, never `now()` in SQL (FR-072) |

Bookkeeping, and deliberately the smallest thing that answers "have any been applied" — which is
unanswerable otherwise, and an empty database is the normal first case rather than an error. Created
by `migrations.pending()` before anything else, so the first run is not a special case. Nothing
hashes file contents, which is a real gap and a smaller one than a checksum table that fires on a
whitespace change.

## Relationship to existing models

```text
Run  ──(submits)──►  pipeline.run()  ──(returns)──►  PipelineResult
 │                                                         │
 │◄──────── six fields copied on completion ───────────────┘
 │
 └──(names)──►  processing_id  ──►  the terminal artifact  ──►  GET /v1/jobs/{id}/result
```

The run row **points at** the artifact chain and never duplicates it. This is what keeps SC-001
achievable: the asynchronous path and the synchronous path converge on the same artifact, so
"identical results" is a property of the design rather than something two code paths must be kept in
agreement about.
