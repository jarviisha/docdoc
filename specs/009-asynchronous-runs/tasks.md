---

description: "Task list for Milestone 9 — Asynchronous Runs, Shared Storage, and Tenant Scoping"
---

# Tasks: Asynchronous Runs, Shared Storage, and Tenant Scoping

**Input**: Design documents from `/specs/009-asynchronous-runs/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md),
[data-model.md](./data-model.md), [contracts/](./contracts/)

**Tests**: Not optional here. The template's constitution override makes a dependency-direction test
mandatory for any layer-boundary change, and this milestone adds a layer. Beyond that, SC-001 —
asynchronous and synchronous results agreeing on every value, verdict, location, and identity — *is*
a test, and it is the criterion the milestone exists to satisfy.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on incomplete work)
- **[Story]**: US1–US6 from spec.md. Setup, Foundational, and Polish carry no label

## Path Conventions

Single project: `src/docdoc/`, `tests/` at repository root, per plan.md's Structure Decision.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Make the four-container topology runnable so later phases have somewhere to run.

- [X] T001 Add `postgres` and `s3` optional extras (`psycopg[binary,pool]>=3.1`, `boto3>=1.34`) to `[project.optional-dependencies]` in `pyproject.toml`, each with a comment stating why it is an extra and not a base dependency, matching the existing extras' style
- [X] T002 Add `docdoc.runs` to the `layers` contract in `pyproject.toml` as `"docdoc.recording : docdoc.runs"`, and add an `independence` contract over `["docdoc.recording", "docdoc.runs"]`, with a comment giving the R10 reasoning
- [X] T002a Add a `forbidden` contract in `pyproject.toml` with `source_modules = ["docdoc.api"]` and `forbidden_modules = ["docdoc.runs.worker"]`. The layers contract puts `api` *above* `runs`, so it may import anything there — the second half of FR-044 is unfalsifiable without this
- [X] T002b Add a `forbidden` contract in `pyproject.toml` with `source_modules = ["docdoc.runs"]` and `forbidden_modules = ["celery", "kombu", "redis", "kafka", "confluent_kafka", "temporalio", "rq", "dramatiq"]`, enforcing FR-026's prohibition instead of trusting review to catch it
- [X] T003 [P] Create the package skeleton `src/docdoc/runs/` with `__init__.py`, and empty `model.py`, `identity.py`, `queue.py`, `postgres.py`, `worker.py`, `errors.py`, `observe.py` plus a `migrations/` directory
- [X] T004 [P] Register `postgres` and `s3` pytest markers in `pyproject.toml` so the offline suite excludes infrastructure-dependent tests by default, and document the two commands in `CONTRIBUTING.md`
- [X] T005 Write `packaging/docker/Dockerfile` — one image, entry point selected at runtime, no second image for the worker
- [X] T006 Write `packaging/docker/compose.yml` with `api`, `worker`, `postgres`, and `minio`, runnable with no cloud credentials and reaching a working deployment with no manual step beyond a schema path and a provider key (FR-076, FR-077, SC-014). Two commands total — `docker compose up -d` then `docdoc migrate`; no `Makefile` or task runner is added, because the repository has neither and quickstart.md already names both commands

**Checkpoint**: `docker compose up` reaches a healthy Postgres and MinIO. Nothing docdoc-specific runs yet.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The run model, the queue, and the two decisions that gate code.

**⚠️ CRITICAL**: No user story work begins until this phase completes.

### Decisions that gate code

- [x] T008 Write `docs/adr/0014-tenant-scoping-and-store-namespacing.md` and mark it Accepted: per-tenant namespacing of the content-addressed store, the cross-tenant existence oracle it closes on **cost and timing** rather than only on response bodies, the cross-tenant reuse it forfeits, the default tenant's namespace being the store root (FR-084a), and authentication defaulting to off (FR-090). Add its row to `docs/adr/README.md` — **done 2026-08-28**
- [x] T009 Amend FR-040 in `specs/009-asynchronous-runs/spec.md` from "without modification to it" to "without changing its behaviour for existing callers", cross-referencing R4 and plan.md's Complexity Tracking — **done 2026-08-28**, applied during `/speckit-analyze` remediation so the contradiction did not stay live in the artifacts while T076 waited

### The run model

- [X] T010 [P] Implement `RunStatus` (five states — no `expired`; see data-model.md transition rule 6) and `Run` in `src/docdoc/runs/model.py` as pydantic models with no I/O, per data-model.md
- [X] T011 [P] Implement `RunError` and its subclasses — including `RunAbandonedError`, `RunNotCancellableError`, `RunStateUnavailableError` — in `src/docdoc/runs/errors.py`, typed and provider-neutral (FR-074)
- [X] T012 Implement `src/docdoc/runs/identity.py`: `new_run_id()`, `now()`, and deadline arithmetic. **The only module in the package permitted to import `uuid`, `time`, `datetime`, `random`, or `secrets`** (R11, FR-072)
- [X] T013 Implement `RunOutcome.of(result: PipelineResult)` in `src/docdoc/runs/model.py` — a projection copying six fields, with no translation and no conditional on schema or document type (R2)
- [X] T014 [P] Implement `tenant_root(tenant_id)` in `src/docdoc/artifacts/paths.py`: **the empty string for the default tenant** and `t/<tenant_id>/` for every other, ahead of the existing two-character fan-out, changing no identity derivation (R12, FR-084a, FR-085). The docstring must state why the default tenant is unprefixed, so the branch is not tidied away later

### The queue

- [X] T015 Define the `RunQueue` Protocol in `src/docdoc/runs/queue.py` with `now` and `run_id` as parameters on every method that needs them, never read inside (contracts/runs-layer.md)
- [X] T016 [P] Implement `InMemoryRunQueue` in `tests/fixtures/run_queue.py` satisfying the same Protocol, so claim policy is testable with no database
- [X] T017 Write `src/docdoc/runs/migrations/0001_runs.sql`: the `runs` table, the check constraint enforcing `processing_id IS NOT NULL` **iff** `status = 'succeeded'`, and the four indexes of data-model.md. Include `tenant_id` from creation — it is the column that cannot be added later (FR-062) — and add **no index on `expires_at`**, which nothing in this milestone queries
- [X] T018 Implement the migration runner and `docdoc migrate [--check]` in `src/docdoc/cli/commands/migrate.py`: numbered plain-SQL files, an applied-versions table, idempotent, never run implicitly at process start (R7, FR-078)
- [X] T019 Implement `PostgresRunQueue.submit()` and `.get()` in `src/docdoc/runs/postgres.py`, with tenant scoping expressed **in the query** rather than as a check after the fetch (FR-063, FR-066)
- [X] T020 Implement `PostgresRunQueue.claim()` using the single-statement `UPDATE … WHERE run_id = (SELECT … FOR UPDATE SKIP LOCKED)` of R8 in `src/docdoc/runs/postgres.py`, with `now` passed as a parameter
- [X] T021 Implement `.heartbeat()` returning `False` when the lease was already lost, `.release()`, and `.finish()` in `src/docdoc/runs/postgres.py`
- [X] T022 Implement `.cancel()` in `src/docdoc/runs/postgres.py`: immediate for `queued`, request-only for `running`, refused with the current state named for terminal states, idempotent for `cancelled` (FR-027, FR-031, FR-034)
- [X] T023 Implement idempotency in `src/docdoc/runs/postgres.py` via the partial unique index on `(tenant_id, idempotency_key)`, returning the original run on conflict rather than reading first (R15, FR-011)

### Foundational tests

- [X] T024 [P] Unit-test the state machine in `tests/unit/test_run_state_machine.py`: every legal transition, every terminal state refusing further transitions (FR-007), that no code path deletes a row, and both invariants of data-model.md
- [X] T025 [P] Unit-test claim policy against `InMemoryRunQueue` in `tests/unit/test_claim_policy.py`: oldest-first (FR-024), lease expiry making a run eligible, attempt increment, and the attempt limit — all at arbitrary `now` values, with no database
- [X] T026 [P] Unit-test determinism confinement in `tests/unit/test_runs_clock_confinement.py`: assert no module in `docdoc.runs` except `identity.py` imports `uuid`, `time`, `datetime`, `random`, or `secrets` (R11)
- [X] T027 [P] Assert `uv run lint-imports` passes with the new layer and independence contracts, and that the existing `tests/unit/test_kernel_purity.py` is unmodified (FR-073)
- [X] T028 Integration-test `PostgresRunQueue` in `tests/integration/test_run_queue_postgres.py` under the `postgres` marker: concurrent claims never hand one run to two workers, and `SKIP LOCKED` lets a second worker claim the second-oldest rather than blocking

**Checkpoint**: runs can be submitted, claimed, and finished from Python. No HTTP, no worker process yet.

---

## Phase 3: User Story 1 — Submit a slow document without holding a connection open (Priority: P1) 🎯 MVP

**Goal**: A document is accepted immediately, executed out of band, and its result retrieved later.

**Independent test**: Submit a document, observe a `run_id` returned before the pipeline has executed,
poll to `succeeded`, and fetch a result identical to the synchronous route's.

### Tests for User Story 1

- [X] T029 [P] [US1] Write `tests/contract/test_async_matches_sync.py` — the same document and schema through both paths, compared on every value, verdict, location, and identity (SC-001). **This is the criterion the milestone exists to satisfy; write it before the worker, and let it fail**
- [X] T030 [P] [US1] Write `tests/contract/test_submission_is_immediate.py` — submission returns under 200 ms at p95 with the worker pool stopped, so no run can complete during measurement (SC-002)
- [X] T031 [P] [US1] Write `tests/integration/test_two_runs_one_result.py` — the same document twice yields two `run_id`s, one `processing_id`, and zero parser invocations on the second, counted rather than timed (FR-005)

### Implementation for User Story 1

- [X] T032 [US1] Implement the worker loop in `src/docdoc/runs/worker.py`: claim → heartbeat → `pipeline.run()` → `finish()`, calling the pipeline once and holding no copy of the stage order. **One run at a time**; the heartbeat timer is the only thread the process has (FR-025, R9a, contracts/runs-layer.md)
- [X] T033 [US1] Add `docdoc worker [--lease-seconds 90] [--max-attempts 3]` — **no `--concurrency` flag**, per FR-025 and R9a — in `src/docdoc/cli/commands/worker.py`, registered as a subcommand of the existing single console script, resolving schemas, adapters, and limits from the **same** configuration vocabulary the API uses with no second set of variable names (FR-041)
- [X] T034 [US1] Implement clean shutdown on `SIGTERM` in `src/docdoc/runs/worker.py`: stop claiming, then finish or `release()` so the run re-queues immediately rather than waiting out its lease (FR-042, FR-043)
- [X] T035 [US1] Add `POST /v1/documents/{blob_id}/runs` to `src/docdoc/api/app.py`, returning 202 with `run_id` and **omitting** `processing_id` rather than nulling it, per contracts/runs-http-api.md and ADR-0012 §3's precedent
- [X] T036 [US1] Add `GET /v1/runs/{run_id}` to `src/docdoc/api/app.py`, returning any of the five states, with 404 for unknown and for another tenant's alike
- [X] T037 [US1] Add `SubmissionResponse`/`RunResponse` models to `src/docdoc/api/models.py`, sharing a base with the existing run responses so they cannot drift in a field neither is defined to differ in
- [X] T038 [US1] Wire run-state configuration into `src/docdoc/api/settings.py` — `DOCDOC_RUN_DATABASE_URL`, `DOCDOC_RUN_LEASE_SECONDS`, `DOCDOC_RUN_MAX_ATTEMPTS` — following the existing explicit-argument-over-environment-over-default precedence, and give each a flag unless it is a credential (FR-083)
- [X] T039 [US1] Verify `GET /v1/jobs/{job_id}` and `GET /v1/jobs/{job_id}/result` are byte-identical in behaviour to Milestone 8 with `tests/contract/test_jobs_routes_unchanged.py`, pinning the three-status set, asserting `pending` never appears (FR-008), and asserting `POST /v1/extract` exposes no asynchronous variant (FR-010)

**Checkpoint**: US1 is independently demonstrable. T029 passes, which is the milestone's headline claim.

---

## Phase 4: User Story 2 — Find out why a run failed, without having been there (Priority: P1)

**Goal**: A failure that nobody was holding a connection for is still fully retrievable.

**Independent test**: Configure a failing adapter, submit, and confirm the terminal state names the
stage and error class and carries the completed stages' outcomes.

### Tests for User Story 2

- [X] T040 [P] [US2] Write `tests/integration/test_failed_run_is_recorded.py` — an extraction failure yields `status: failed`, a named `failed_stage`, a named `error_class`, and no `processing_id`
- [X] T041 [P] [US2] Write `tests/integration/test_run_record_leaks_nothing.py` — over a document seeded with distinctive strings, assert 0% of document text, extracted values, claimed text, prompt bodies, and provider messages appear in any run record or log line (SC-007)
- [X] T042 [P] [US2] Write `tests/integration/test_poison_run.py` — a document that terminates the worker comes to rest at `failed` with `error_class: "RunAbandonedError"` within the attempt limit, terminating at most that many workers (SC-006)

### Implementation for User Story 2

- [X] T043 [US2] Persist `failed_stage`, `error_class`, and `stage_outcomes` in `PostgresRunQueue.finish()` in `src/docdoc/runs/postgres.py`, copied from `PipelineResult` and narrowed to the four fields that survive the no-content rule (R2, FR-035, FR-036)
- [X] T043a [US2] Implement the unresolvable-schema path in `src/docdoc/runs/worker.py` and `src/docdoc/runs/postgres.py`: resolve `schema_identity` before calling the pipeline, and on failure finish the run terminally with a schema error class, a null `failed_stage`, and no re-queue or retry (FR-091)
- [X] T043b [P] [US2] Write `tests/integration/test_schema_withdrawn_between_submit_and_claim.py` — queue a run, remove the schema from the registry, start the worker; the run is `failed` with a schema error class and a null `failed_stage`, is claimed exactly once, and never reports `RunAbandonedError` (FR-091, FR-038)
- [X] T044 [US2] Implement the abandonment transition in `src/docdoc/runs/postgres.py`: at the attempt limit, move to `failed` with `RunAbandonedError` and stop the run being claimable (FR-021, FR-038)
- [X] T044a [P] [US2] Implement `src/docdoc/runs/observe.py` emitting one `run.transition` event per state change — `run_id`, `tenant_id`, `from_state`, `to_state`, `attempts`, `worker_id`, `reason` — via standard-library `logging`, with a module docstring arguing why this does not violate `pipeline/observe.py`'s refusal of a run-level event (FR-092, R10a)
- [X] T044b [P] [US2] Write `tests/unit/test_run_events_carry_no_content.py` — over a run seeded with distinctive strings, assert `run.transition` payloads contain no document text, extracted value, claimed text, prompt body, credential, or provider message, and no duration, token count, cost, or stage result (FR-092, FR-093)
- [X] T045 [US2] Surface `failed_stage`, `error_class`, and `stage_outcomes` in the `GET /v1/runs/{run_id}` response in `src/docdoc/api/app.py`
- [X] T046 [US2] Assert in `tests/unit/` that `error_class` can only ever be a class name — the value is read from `PipelineResult`, which already reduced it, so the test pins that the projection never substitutes a message (FR-037)

**Checkpoint**: US1 + US2 together are a usable asynchronous engine on a single worker with a local store.

---

## Phase 5: User Story 3 — Run more than one worker without paying twice (Priority: P2)

**Goal**: Scaling workers does not re-pay for parses another worker already performed.

**Independent test**: Two workers, one shared store, same document twice — parser invocations: zero on
the second.

### Tests for User Story 3

- [X] T047 [P] [US3] Write `tests/integration/test_shared_store_reuse.py` — two workers on one MinIO bucket; a document already parsed and resubmitted by the **same tenant** reuses the parse, counted on a parser invocation counter (SC-005)
- [X] T048 [P] [US3] Write `tests/integration/test_redelivery.py` — kill the worker at each of the four stage boundaries; every run completes on redelivery with an identical `processing_id`, and billable invocations exceed the uninterrupted count by at most one, by zero between stages (SC-003, SC-004)
- [X] T049 [P] [US3] Write `tests/integration/test_restart_survival.py` — restart the API and every worker mid-run; 100% of in-flight runs reach a terminal state with no operator action (SC-011)
- [X] T050 [P] [US3] Write `tests/integration/test_s3_store_rules.py` — the six rows of contracts/runs-layer.md's table: miss, format mismatch, content mismatch raising, unreachable degrading, identical write as no-op, divergent write raising. Plus a store-equivalence case: the same document through the filesystem store and the S3 store agrees on every value, verdict, location, and identity (FR-051) — a cheap copy of SC-001 aimed at the store rather than the transport

### Implementation for User Story 3

- [X] T051 [US3] Implement `S3ArtifactStore` in `src/docdoc/artifacts/s3.py` satisfying the existing `ArtifactStore` Protocol, with `boto3` imported lazily in the constructor so a base install neither imports nor requires it (R5)
- [X] T052 [US3] Implement `S3BlobStore` in `src/docdoc/artifacts/s3.py` on the same terms
- [X] T053 [US3] Ensure both stores in `src/docdoc/artifacts/s3.py` inherit ADR-0010 §4 and §5 rather than restating them — degradation, format mismatch, content mismatch, and the no-overwrite rule — sharing the filesystem store's logic where it is store-agnostic (R3, FR-047, FR-048)
- [X] T054 [US3] Wire store selection into `src/docdoc/api/settings.py` via `DOCDOC_STORE_URL`, keeping the filesystem store the default and unchanged (FR-050)
- [X] T055 [US3] Apply `tenant_root()` in both stores' key derivation in `src/docdoc/artifacts/s3.py` and in the filesystem store, so namespacing is on from the first commit and the default tenant resolves to the **unprefixed** legacy layout (FR-084, FR-084a, FR-088). No relocation, no copy, no read-through fallback
- [X] T056 [P] [US3] Correct the `pyproject.toml` layers comment claiming `artifacts` "depends on `pydantic` and two kernel hashing helpers and on nothing else" — it stops being true in this change, and a comment that lies is worse than one that is absent (R5)

**Checkpoint**: multi-worker deployment is safe. The reuse guarantee holds across processes.

---

## Phase 6: User Story 4 — Deploy it behind a load balancer (Priority: P2)

**Goal**: Both process types can be scheduled and taken out of rotation correctly.

**Independent test**: Stop the database; liveness passes, readiness fails naming the dependency,
submission is refused retryably.

### Tests for User Story 4

- [X] T057 [P] [US4] Write `tests/integration/test_health_endpoints.py` — with Postgres stopped: liveness 200 in 100% of probes, readiness 503 in 100% naming `run-state-database`, submission 503 retryable in 100% and accepted-then-lost in 0% (SC-009)
- [X] T058 [P] [US4] Write `tests/unit/test_health_discloses_nothing.py` — neither route returns configuration values, credentials, tenant identifiers, or counts of stored content (FR-058); and probe readiness with adapter and parser counters attached, asserting both stay at zero, because a readiness check that bills the deployment is worse than none (FR-056)

### Implementation for User Story 4

- [X] T059 [US4] Implement `GET /healthz` in `src/docdoc/api/health.py` returning a constant and touching no database, store, or provider (FR-053)
- [X] T060 [US4] Implement `GET /readyz` in `src/docdoc/api/health.py`: one `SELECT 1`, one store metadata call against a fixed key, short timeout, two-second cache, naming the unmet dependency on failure (R13, FR-054, FR-055)
- [X] T061 [US4] Make readiness **strict** in `src/docdoc/api/health.py` — a process that cannot reach the database reports not ready even though the synchronous routes still work (FR-087) — and state in the docstring that this withdraws working capacity on purpose
- [X] T062 [US4] Refuse `POST /…/runs` in `src/docdoc/api/app.py` with a retryable 503 when the run-state database is unreachable, rather than accepting work that cannot be recorded (FR-057, gate 9)
- [X] T063 [US4] Serve both routes from the worker process too via `src/docdoc/runs/worker.py`, on the same terms, so one orchestrator configuration covers both process types (FR-053, FR-054)
- [X] T064 [US4] Register health routes in `src/docdoc/api/app.py` outside `/v1` and outside the authentication dependency (FR-058)

**Checkpoint**: the composition is schedulable by an orchestrator.

---

## Phase 7: User Story 5 — Keep one tenant out of another tenant's results (Priority: P2)

**Goal**: A shared deployment is safe for more than one customer.

**Independent test**: Two keys; each identifier — run, blob, processing — unreadable under the other.

### Tests for User Story 5

- [X] T065 [P] [US5] Write `tests/contract/test_tenant_isolation.py` — cross-tenant access to `run_id`, `blob_id`, and `processing_id` returns responses **byte-identical** to those for a non-existent identifier, in status and body (SC-008)
- [X] T066 [P] [US5] Write `tests/contract/test_no_existence_oracle.py` — a tenant submitting a document another tenant already processed invokes the parser and the model adapter exactly as many times as a first-ever submission (SC-017). **This is the test the namespacing decision exists for; a status code cannot satisfy it**
- [X] T067 [P] [US5] Write `tests/integration/test_upgrade_compatibility.py` — seed a store in the Milestone 8 layout, then with authentication at its default assert previously stored blobs and artifacts stay readable **at their original paths**, that reuse still hits, and that existing routes return what they returned before; after enabling authentication, 0% of pre-existing content is unreachable and nothing was copied or moved (SC-018, FR-084a)
- [X] T068 [P] [US5] Write `tests/unit/test_idempotency_scope.py` — one key twice under one tenant produces one run; the same key under two tenants produces two (SC-016)

### Implementation for User Story 5

- [X] T069 [US5] Implement `src/docdoc/api/auth.py`: load a key file named by `DOCDOC_API_KEYS_FILE`, resolve a key to a principal carrying exactly one `tenant_id`, compare with `secrets.compare_digest` over a SHA-256 (R14, FR-060, FR-061)
- [X] T070 [US5] Make authentication **disabled by default** in `src/docdoc/api/auth.py`, with one implicit tenant owning everything and behaviour identical to Milestone 8 (FR-088)
- [X] T071 [US5] Validate `tenant_id` as `[a-z0-9_-]{1,64}` at the auth boundary in `src/docdoc/api/auth.py` so a value that could escape a path segment never reaches a store, and do not re-validate in the stores (R12)
- [X] T072 [US5] Apply the authentication dependency in `src/docdoc/api/app.py` to every route except `/healthz` and `/readyz`, rejecting before any document is read, provider called, or store touched (FR-059, FR-067)
- [X] T073 [US5] Scope blob reads and the existing job routes by tenant in `src/docdoc/api/app.py`, returning the non-existence response for another tenant's identifier (FR-064, FR-065, FR-066)
- [X] T074 [US5] Assert in `tests/unit/test_credential_never_logged.py` that no credential reaches a log line, a run record, an error body, or a process argument list (FR-068) with a test over the seeded-string fixture
- [X] T074a [P] [US5] Write `tests/unit/test_cli_and_library_need_no_credential.py` — with `DOCDOC_API_KEYS_FILE` set, every CLI subcommand and every in-process library entry point still runs with no credential supplied (FR-069). Authentication is an HTTP concern and this is the test that keeps it one
- [X] T075 [US5] Add `src/docdoc/runs/migrations/0002_default_tenant.sql` plus the explicit, idempotent step that assigns pre-existing content to a configured default tenant, inferring no owner and leaving nothing unreachable (FR-089)

**Checkpoint**: the five deployment-critical stories are complete and the deployment is multi-tenant safe. US6 remains, and nothing is blocked on it.

---

## Phase 8: User Story 6 — Stop a run that should not have been submitted (Priority: P3)

**Goal**: A run that should not proceed stops before it spends money, and says honestly what it could
not recall.

**Independent test**: Cancel a queued run — it never executes. Cancel a running one — it stops before
the next billable stage while the stage in flight completes.

> **On this phase's history.** It was written as a bare "Phase 8: Cancellation" with no story label,
> because spec.md had five user stories and none of them cancelled a run — eight functional
> requirements and a success criterion with no journey behind them. `/speckit-analyze` raised it on
> 2026-08-28 and User Story 6 was added rather than the requirements being absorbed into a story they
> did not belong to.

- [X] T076 [US6] Add `should_continue: Callable[[], bool] | None = None` to `pipeline.run()` in `src/docdoc/pipeline/runner.py`, consulted **only at stage boundaries**, default preserving existing behaviour byte for byte (R4)
- [X] T077 [P] [US6] Unit-test in `tests/unit/test_pipeline_cancellation.py` that `should_continue=None` produces results identical to before, and that returning `False` stops before the next stage while leaving completed stages' artifacts written and yielding no `processing_id`
- [X] T078 [US6] Consult the cancellation flag from the worker loop in `src/docdoc/runs/worker.py` (contracts/runs-layer.md)
- [X] T079 [US6] Add `DELETE /v1/runs/{run_id}` to `src/docdoc/api/app.py` — 200 for queued and running alike, 409 naming the state for terminal runs, 404 for another tenant's (FR-031, FR-063)
- [X] T080 [P] [US6] Write `tests/integration/test_cancellation.py` — a queued run never executes; a running run stops before the *next* billable stage; 0% of in-flight provider calls are aborted (SC-015)
- [X] T081 [US6] Document in contracts/runs-http-api.md and the concept doc that a 200 on a running run means *requested*, not *stopped*, and that a provider call already in flight completes and is billed (FR-029)

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T082 [P] Write `docs/concepts/runs.md` covering the two identities, the run lifecycle, redelivery, and the exact limits of cancellation (FR-079)
- [X] T083 [P] Write `examples/submit_async_run.py` — submit and poll to completion, runnable against the composition with the `echo` adapter (FR-080)
- [X] T084 [P] Update the README: roadmap row for Milestone 9, the Over HTTP section with the three run routes, and the configuration section with every new setting (FR-081)
- [X] T085 Revise the README's unauthenticated warning to state what is now true — that authentication exists, is off by default, and a deployment which has not enabled it is exactly as exposed as before (FR-081). **The warning must not be deleted**; a deployment at the default is unchanged
- [X] T086 [P] Update `examples/serve_api.md` to cover running a worker alongside the API
- [X] T087 Verify SC-010: run `examples/evaluate_golden_set.py` and confirm golden-set metrics are **bit-identical** to the pre-milestone output. Any movement is evidence the scope escaped, not a regression to investigate
- [X] T088 Verify SC-012: `git diff --stat` against the merge base touches **zero** files under `src/docdoc/kernel`, `ingest`, `extraction`, `grounding`, `validation`, and zero lines of the artifact envelope's identity derivation
- [X] T089 Verify SC-013: a base install acquires zero new dependencies, and `uv run pytest tests/unit tests/property` passes with neither `docdoc[postgres]` nor `docdoc[s3]` installed and no database or bucket present
- [X] T090 Walk `quickstart.md` end to end on a clean checkout and confirm the 10-minute claim (SC-014); correct the document where it is wrong rather than the timing where it is inconvenient
- [X] T091 Flip `specs/009-asynchronous-runs/spec.md` Status from `Draft` to `Implemented` and update the README roadmap table in the same change, per the status vocabulary's wiring
- [X] T092 [P] Run `uv run ruff check .`, `uv run mypy src/docdoc/runs`, and `uv run lint-imports`, and confirm `HYPOTHESIS_PROFILE=thorough uv run pytest` is green

---

## Dependencies & Execution Order

### Phase dependencies

```text
Phase 1 Setup
   └─► Phase 2 Foundational  (T008 gates everything; T009 done; T017 gates T019–T023)
          ├─► Phase 3 US1  (P1) ──┐
          │      └─► Phase 4 US2 (P1) — shares finish(); US2 extends what US1 writes
          ├─► Phase 5 US3  (P2)    │   independent of US2
          ├─► Phase 6 US4  (P2)    │   independent of US2, US3
          └─► Phase 7 US5  (P2)    │   depends on T014 tenant_root and T055
                                   │
                 Phase 8 US6 (P3) ────► depends on Phase 3 (worker loop)
                                   │
                 Phase 9 Polish ◄──┘   depends on all
```

### Within each user story

Tests are written first and allowed to fail. T029 in particular should be red before the worker
exists, because a green SC-001 test that was written after the code is a test that was fitted to it.

### Parallel opportunities

- **Phase 2**: T010, T011, T014, T016 are four files with no shared state — parallel. T024–T027 likewise.
- **Phase 3–7**: US3, US4, and US5 touch disjoint files (`artifacts/s3.py`, `api/health.py`,
  `api/auth.py`) and can proceed concurrently once Phase 2 lands. US2 is the one that must follow US1,
  because it extends `finish()`.
- **All test-writing tasks marked [P]** across Phases 3–7 can be written before any of their
  implementation exists.

### Parallel example: after Phase 2

```text
Developer A: T029–T039  (US1 — the MVP)
Developer B: T047, T050–T056  (US3 — S3 stores; only T055 waits on nothing in US1)
Developer C: T057–T064  (US4 — health)
Developer D: T065–T075  (US5 — auth and scoping)
```

---

## Implementation Strategy

### MVP first (User Story 1 only)

T001–T039. That is a working asynchronous engine: one worker, a filesystem store, no authentication,
no health routes. It is deployable on a single node and it satisfies SC-001, which is the criterion
the milestone exists for. Stop here and the topology change is proven before any of the scaling work
is built on it.

### Incremental delivery

1. **US1** — asynchronous execution works and matches synchronous output.
2. **US2** — failures survive the absence of a caller. US1 + US2 is the honest single-node deployment.
3. **US3** — multiple workers become safe. This is the one whose absence is *invisible*: without it,
   scaling produces correct results while silently re-paying for every parse.
4. **US4** — schedulable behind a load balancer.
5. **US5** — safe for more than one customer.
6. **US6** — cancellation. Last because nothing is stuck without it: every run reaches a terminal state
   regardless. What it buys is money, not liveness.

### Notes

- **T029 is the gate for everything.** If asynchronous and synchronous results ever disagree, no other
  task in this list matters.
- **T009 is done, and it was not optional bookkeeping.** Shipping the `should_continue` parameter
  while FR-040 still said "without modification to it" would leave merged code contradicting its own
  specification — the exact failure ADR-0013 exists to prevent one level up.
- **T002a and T002b enforce two requirements that review cannot.** FR-044's second half and FR-026 are
  both unfalsifiable without an import contract: `api` sits above `runs` and may import anything
  there, and nothing stops a broker dependency arriving in a later PR.
- **T087 and T088 are scope alarms, not chores.** This milestone touches no stage; a moved metric or a
  changed kernel file means something escaped.

---

## Phase 10: Convergence

Appended by `/speckit-converge` on 2026-09-03, after Phases 1–9 were complete. Every item below is
a gap between what `spec.md` and `contracts/` call for and what the code does — assessed against the
present state of the tree, not against a diff. No constitution MUST principle is violated: the
kernel is untouched, all eleven import contracts hold, and the determinism guard is green.

Two of these are the same species of gap as the six test files Phase 9 found missing: a task marked
done whose implementation covers a narrower case than the requirement states.

- [X] T093 Emit `run.transition` at **every** state change, not only the terminal one, per FR-092 (partial). `log_transition` has one call site — `worker._finish` — so the claim (`queued→running`), the lease-expiry redelivery (`running→queued`), the abandonment transition in `PostgresRunQueue.claim`, and the API's `DELETE /v1/runs/{run_id}` all change state silently. `runs/observe.py`'s own docstring names those five as the reason the module exists ("a claim, a lease expiry, a redelivery, a cancellation, an abandonment — none has a stage to attach to"), so the module's intent is already broader than its callers. Lease handoff between workers is the hardest thing in a four-process topology to debug, and it is currently the only thing that logs nothing
- [X] T094 [P] Extend `tests/unit/test_run_events_carry_no_content.py`, or add a sibling, to assert **coverage** rather than only payload shape per FR-092 (partial). The existing test pins that a transition event carries seven fields and no content; nothing asserts that a transition *produces* an event. That is why T044a could be marked done with one call site — write the test that would have failed
- [X] T095 Make the worker relinquish a run in flight on `SIGTERM` per FR-042 and FR-043 (partial). `release()` exists on the protocol, on `PostgresRunQueue`, and on `InMemoryRunQueue`, and **nothing calls it**. `Worker._execute_with_heartbeat` consults `_stopping` only after `execute_one` returns, so a signalled worker always runs the current document to completion — and since a run takes minutes while an orchestrator's grace period is seconds, the process is killed mid-run and the run then waits out its full lease. Compose the shutdown flag into the `should_continue` callback the cancellation work already added, so the run stops at the next stage boundary, then `release()` it. FR-043's "immediately rather than waiting for lease expiry" is currently unreachable code
- [X] T096 [P] Write the redelivery-latency test FR-043 needs (partial): a worker signalled mid-run releases its claim, and the run is claimable at the next instant rather than after `DEFAULT_LEASE`. Drivable against `InMemoryRunQueue` at arbitrary `now` values, with no sleeping and no database
- [X] T097 Return `200` rather than `202` on an idempotent replay of `POST /v1/documents/{blob_id}/runs`, per contracts/runs-http-api.md (contradicts). The contract tabulates `200` for "idempotency key already seen for this tenant; body is the original run"; the route is declared `status_code=202` and answers 202 for both cases. FR-011 is satisfied — the original `run_id` comes back — but a caller cannot tell "accepted, work queued" from "this is the run you already had", which is the whole reason the contract names two codes. `PostgresRunQueue.submit` already distinguishes the two paths internally; the distinction is lost before it reaches the response. **If the single status is deliberate, amend the contract instead** — but one of the two documents has to move
- [X] T098 [P] Assert the idempotent-replay status in `tests/unit/test_idempotency_scope.py` or a contract test (partial). That file covers the *rule* against the queue and never crosses the HTTP boundary, which is why the status-code divergence survived
- [X] T099 Bring `/ui` under the authentication dependency, or record its exemption in spec.md, per FR-059 (contradicts). With a key file configured, `GET /ui` and `GET /ui/index.html` return **200 with no credential** — `_mount_ui` calls `app.mount` on the application, outside the `/v1` router that carries `Depends(principal_of)`. FR-059 exempts liveness and readiness and nothing else. The assets carry no tenant data, so this is a scoping question rather than a leak; but the README now tells a reader that enabling authentication "covers `/ui` too", and for the static assets that sentence is false. Decide which way, and make the document and the code agree
- [X] T100 [P] Add the assertion that would have caught T099 (missing): with authentication enabled, every mounted path except `/healthz` and `/readyz` answers 401 without a credential. Enumerated from the application's own route and mount table, so a mount added later is covered without anybody remembering
- [X] T101 Resolve `DOCDOC_DEFAULT_TENANT`'s missing flag against FR-083 (contradicts). FR-083 permits exactly two exemptions from "MUST gain a flag" — a credential, or meaningless outside one process type — and this setting is neither: it is meaningful in the API, in every worker, and in `docdoc migrate`. The reason recorded in `cli.config.ENVIRONMENT_ONLY` ("describes the store's layout and must be identical in every process") is a third category the requirement does not admit. Either add `--default-tenant`, or amend FR-083 to name deployment-wide layout settings as a third exemption. The exemption is defensible; it is just not currently sanctioned by the text it is measured against
- [X] T102 [P] Close the parity check's blind spot for flag-only settings per FR-083 (partial). `--health-port` was introduced with no paired `DOCDOC_*` variable, so it appears in neither `FLAG_FOR_SETTING` nor `ENVIRONMENT_ONLY` and `test_cli_config_vocabulary.py` — which enumerates environment names — cannot see it at all. Either pair it with a variable, or extend that check to walk the parser as well as the environment, so a flag with no setting behind it is a decision rather than an omission

**Note on what convergence did *not* find.** Every success criterion was verified rather than
assumed: SC-001, SC-002, SC-009, SC-014 and SC-015 against the running four-container composition;
SC-010 bit-identical to the pre-milestone golden-set output; SC-012 zero files under the five
untouched layers; SC-013 in a virtual environment with no extras installed at all. No `missing` gap
and no `unrequested` code was found — every addition beyond the task list (`ping`, `probe`,
`--store-url`, `--health-port`, `DOCDOC_DEFAULT_TENANT`, the `DOCDOC_EXTRAS` build argument) traces
to a requirement that could not otherwise be met, and the two that trace only loosely are T101 and
T102 above.

---

## Phase 11: Convergence

Appended by `/speckit-converge` on 2026-09-03, after Phase 10 closed the first round's findings.
**No code gap this pass.** Every item below is a document that no longer describes the code, or a
deployment artifact that cannot do what the specification says it does. No constitution MUST
principle is violated; the eleven import contracts hold, the kernel is untouched, and the golden-set
output is bit-identical to the pre-milestone baseline.

The pattern in three of the four is worth naming, because it is the same one `/speckit-analyze`
raised at Milestone 7: **an amendment reconciled in one document and missed in the others.** FR-081's
"the unauthenticated warning MUST be revised" was applied to the README, and the identical sentence
in three other files was not.

- [X] T103 Forward the **parser** credentials to the containers in `packaging/docker/compose.yml` per FR-077 (missing). The `environment:` block passes `GEMINI_API_KEY` — which is a *model* credential — and none of `DOCDOC_AZURE_DI_ENDPOINT`, `DOCDOC_AZURE_DI_KEY`, `DOCDOC_GCV_CREDENTIALS` or `GOOGLE_APPLICATION_CREDENTIALS`. The image installs the `azure` and `gcv` extras and deliberately omits `pdf` (AGPL, ADR-0001), so **a cloud parser credential is the only route to a working default deployment and there is no way to supply one**. FR-077 permits exactly one manual step beyond a schema path — supplying a provider credential — and that step currently does nothing: every run fails with `ParserCapabilityError` and nothing says why. `GOOGLE_APPLICATION_CREDENTIALS` names a file, so it needs a mount as well as a variable, or a documented note that the `DOCDOC_GCV_CREDENTIALS` form is the one to use in the composition
- [X] T104 [P] Assert that every credential the composition's image can use is forwarded to both process types per FR-077 (missing). Read `compose.yml`, collect the `DOCDOC_*` and provider credential names each installed extra reads, and fail on any the `environment:` block drops. A test rather than a review comment because the failure is silent: the container starts, reports healthy, and refuses every document
- [X] T105 Revise the "it is unauthenticated" warning in `docs/concepts/viewer.md`, `examples/view_grounding.md` and `examples/serve_api.md` per FR-081 (contradicts). All three state it flatly, and it is now false wherever `DOCDOC_API_KEYS_FILE` is set — `/ui` is behind the credential like everything but the two probes. `examples/serve_api.md` is the worst of the three: its new Authentication section says `/ui` requires a credential and line 259 says the viewer is unauthenticated, roughly a hundred lines apart in one file. **The warning must not be deleted** — a deployment at the default is exactly as exposed as it was, which is what T085 established for the README — it must say the same true thing the README now says, including that a browser cannot send a bearer token and so the viewer is unavailable on an authenticated deployment
- [X] T106 Correct three stale claims in `README.md` per FR-081 (partial). It says "twelve accepted ADRs" and there are **fourteen**; its **Status** paragraph reads "Milestones 1 … 6 implemented" while the roadmap table two screens below marks 7, 8 and 9 **Done**, so the document contradicts itself on its own subject; and its Documentation section lists every prior milestone's concept document and contract but neither `docs/concepts/runs.md` nor `contracts/runs-http-api.md` and `contracts/runs-layer.md`. The Status line was already two milestones stale before this one — inherited rather than caused here, and fixed here because this is the milestone that made it three
- [X] T107 [P] Add `docs/concepts/runs.md` and `examples/serve_api.md` to `CONFIG_DOCUMENTS` in `tests/unit/test_documented_api_references_resolve.py` per plan.md's testing approach (partial). Between them they name seven `DOCDOC_*` settings and neither is in the validated set, so a renamed constant would leave both confidently wrong with the suite green — precisely the drift that check exists to prevent, and the reason it was written after four convergence passes missed the same class of gap

**Three documents share this blind spot and are out of scope here**, because `/speckit-converge` is
bounded by this feature's artifacts and these predate it: `docs/concepts/ingest.md` names the two
Azure settings, `docs/concepts/viewer.md` names `DOCDOC_UI_ROOT`, and `examples/view_grounding.md`
names four. `DOCDOC_UI_ROOT` is a second-order case — `docdoc.api.settings` defines it but
`CONFIG_MODULES` does not list it, so nothing requires it to be documented *or* checks the document
that names it. Worth a Milestone 10 task; not this milestone's to claim.

**`CHANGELOG.md` is also out of scope and worth your attention.** Its `[Unreleased]` section
describes Milestone 8 and says nothing about Milestone 9, while Milestones 4 through 8 each wrote a
full entry. No requirement in `spec.md`, `plan.md` or `tasks.md` names it, so converge appends no
task for it — but the repository's convention is unmistakable and a reader of this branch would
expect one.

---

## Phase 12: Convergence

Appended by `/speckit-converge` on 2026-09-03, after Phase 11. **Every finding is a test gap rather
than a behaviour gap**: in all four cases the code does the right thing and nothing pins it. No
constitution MUST principle is violated; the eleven import contracts hold, the kernel is untouched,
and the golden-set output is bit-identical.

All four came from walking the **21 acceptance scenarios and 11 edge cases individually** — the first
pass to do so. The three prior rounds assessed against FR and SC numbers, which is why a scenario with
no coverage survived three of them: nothing in the numbered requirements says "the worker answers a
probe", and the sentence that does is in a user story.

- [X] T108 Start the worker's health server and probe it per US4/AC3, FR-053 and FR-054 (missing). **Nothing in `tests/` references `_HealthServer` or `health_port`** — the whole of `runs/worker.py`'s standard-library server is untested, while `compose.yml` runs it with `--health-port 8000` and the container healthcheck probes it. Every existing health assertion covers the API's routes or the shared `Readiness` object, so "both process types, on the same terms" is verified for one of the two. Bind an ephemeral port, probe `/healthz` and `/readyz`, and assert the bodies are **byte-identical** to the API's — that identity is the whole of what "one orchestrator configuration covers both" means, and comparing bodies is the only assertion that checks it. Cover the 404 for any other path too: that port is reachable by whoever can reach the worker and must describe nothing
- [X] T109 [P] Cover run submission against another tenant's blob per US5/AC3 (missing). `tests/contract/test_tenant_isolation.py` asserts the cross-tenant response for `GET /v1/documents/{blob_id}`, for the synchronous extract route, and for `GET /v1/runs/{run_id}` — and never for `POST /v1/documents/{blob_id}/runs`, which is the route this milestone added and the one the scenario names. The code is correct: `submit_run` reads through `_stores_of`, so another tenant's blob is simply absent and answers 404. Pin it, byte-identically against a blob that never existed, as the neighbouring tests do
- [X] T110 [P] Assert the store degradation is logged **once** per run, not once per stage, per US3/AC3 and the Edge Cases (partial). Both say "logged once", `_Reuse._degrade` implements it with a `_degraded` flag, and the only test — `tests/integration/test_reuse.py:274` — asserts the *outcome* (the run succeeds) and never the count. The untested half is the operationally expensive one: a store outage across a fleet emits four lines per run per replica, and the claim that it does not is currently a docstring. Drive a four-stage run against an unreachable store with `caplog` and assert exactly one `pipeline.store_*` record
- [X] T111 Scrub `GOOGLE_APPLICATION_CREDENTIALS` in `tests/conftest.py` and make the credential list checkable (partial, plan: testing approach). Measured rather than inferred: with the variable set, `default_registry()` reports `gcv` **available**; without it, unavailable. It is set on any machine with `gcloud` configured, so a contributor's offline suite exercises a different parser registry than CI's — which is exactly the failure that file's own docstring names, "it fails in the worst possible way — on the machine that is *correctly* configured for real use, while passing everywhere else". The `DOCDOC_*` half is already prefix-scrubbed and safe; `CREDENTIAL_ENV` is the hand-maintained half and it went stale. Add the name, and add the assertion that would have caught it: every credential-shaped variable the adapters and parsers read is either prefix-scrubbed or named in `CREDENTIAL_ENV`. A hand-maintained list checked against the code is the pattern this repository already uses for `FLAG_FOR_SETTING` and `CONFIG_MODULES`, and for the same reason

**What this pass confirms rather than finds.** All 21 acceptance scenarios were walked: US1's four, US2's
three, US3's three, US4's three, US5's three, and US6's five. Eighteen have coverage that names them.
The three that did not are T108, T109 and T110 above. All 11 edge cases were walked; two —
"the same document submitted twice concurrently" and "a run's row is updated but the process dies
before the response is sent" — are covered in parts rather than as compositions, and are left alone
deliberately: the first is the conjunction of `test_two_runs_one_result.py` and
`test_store_concurrency.py`, and the second asserts that a database row outlives a process, which is a
property of Postgres rather than of docdoc.

---

## Phase 13: Convergence

Appended by `/speckit-converge` on 2026-09-03, after Phase 12. **No behaviour gap and no missing
work.** Two findings, both about a document describing something the code does not do, or a check
that does not reach this milestone. No constitution MUST principle is violated; the eleven import
contracts hold, the kernel is untouched, and the golden-set output is bit-identical.

This pass walked what the previous four did not: the **13 Out-of-Scope exclusions** (nothing built
violates one), the **5 data-model invariants** and **7 transition rules** (all asserted in
`test_run_state_machine.py`), and the **4 indexes**. F2 came from the last of those.

- [X] T112 Extend `tests/unit/test_data_model_matches_the_code.py` to cover this milestone's data model per plan.md's testing approach and FR-079 (partial). That file is hard-wired to `DATA_MODEL = pathlib.Path("specs/006-golden-set-evaluation/data-model.md")`, and this milestone's `data-model.md` carries an 18-row field table for `Run` that **nothing reads**. Its own docstring says why that matters: it exists because `EvaluationReport`'s table named `group_outcomes` and `validation_verdicts` as fields when both lived one level down, and "nothing was reading the tables". Two things make this milestone's table different and the task has to handle both. It describes **database columns, not model fields** — so check it against `0001_runs.sql`, not against the pydantic `Run`. And it lists `cancel_requested`, which is deliberately *not* a `Run` field (`_row_to_run` pops it as transport state), so a naive check against the model would fail on a row that is correct. Measured before writing this: all 18 documented columns exist in the migration, so the check passes today and earns its keep on the next change. Keep the existing one-directional rule — a column the SQL has and the document does not is not a failure, or every additive migration needs a documentation edit before the build goes green
- [X] T113 [P] Correct the claimable index in `specs/009-asynchronous-runs/data-model.md` per its own Indexes table (contradicts). It specifies ``(status, created_at)`` partial `WHERE status IN ('queued','running')`; `0001_runs.sql` creates `ON runs (created_at)` with the same predicate — one key column, not two. **The code is right and the document is wrong**, which is the direction worth being careful about: with the predicate already pinning `status` to two values, a leading `status` column is redundant *and* would stop the index producing globally ordered `created_at` across both values, which is exactly what the claim query's `ORDER BY created_at` needs to avoid a sort. Correct the cell and say why the status column is in the predicate rather than the key, so the next reader does not "fix" it back

**What this pass confirms rather than finds.** Every one of the 21 acceptance scenarios now has
coverage that names it — the three that did not were closed in Phase 12. Every edge case is covered,
two of them as compositions of existing tests rather than as their own, which was recorded in Phase
12 and remains deliberate. Nothing in the code is `unrequested`: every addition beyond the original
task list traces to a requirement that could not otherwise be met, and the two that traced only
loosely were resolved in Phase 11.

**`CHANGELOG.md` remains out of scope and remains worth doing.** Its `[Unreleased]` section describes
Milestone 8 and says nothing about Milestone 9, while Milestones 4 through 8 each wrote a full entry.
Four convergence passes have now declined to append a task for it, because no artifact in this
feature names it and this command is bounded by them. That is the right call for the command and the
wrong outcome for the branch; it is named here a fourth time so the decision to skip it stays a
decision.

---

## Phase 14: Convergence

Appended by `/speckit-converge` on 2026-09-03, after Phase 13. Three findings, and **one of them is a
real operational defect** rather than a document that drifted. No constitution MUST principle is
violated; the eleven import contracts hold, the kernel is untouched, and the golden-set output is
bit-identical.

The previous pass reported that the next one should find nothing. It found these, and the reason is
worth recording because it is a lesson about the method rather than about the code: **this is the
first pass to walk `research.md`'s seventeen decisions individually**, and two of the three came from
there. Five passes had treated R1–R15 as background rather than as the plan decisions `plan.md`
explicitly delegates to them. A checklist that is never enumerated is a checklist that is never
checked — which is the same finding this milestone has now made about documents, data models, and
acceptance scenarios.

- [X] T114 Give the S3 client a short, explicit timeout per R13 and FR-054 (partial). `s3_client()` passes no `botocore.config.Config`, so the store inherits botocore's defaults — 60 s connect, 60 s read, and a retry policy on top. R13 specifies that readiness performs "one store metadata operation against a fixed key, **with a short timeout**", and the database half honours it (`CONNECT_TIMEOUT_SECONDS = 5` on the psycopg factory) while the store half does not. The consequence is worse on the worker than on the API: `_HealthServer` is a `ThreadingHTTPServer`, so every probe against a black-holed endpoint occupies a thread for the full timeout, and an orchestrator probing every five seconds accumulates them faster than they retire. Set connect and read timeouts of a few seconds and cap retries, and say in the docstring why a store probe's timeout must be shorter than the probe interval that calls it. **Note the second-order effect and decide it deliberately**: this client is the same one the *pipeline* uses to read and write artifacts, where a short timeout turns a slow-but-working store into a degraded one. If the two need different timeouts, that is a real distinction and the probe should get its own client rather than both sharing a compromise
- [X] T115 [P] Document `docdoc_default_tenant` and `docdoc_schema_version` in `specs/009-asynchronous-runs/data-model.md` (partial). The milestone creates three tables and the data model describes one. `docdoc_default_tenant` is the one that matters: it carries FR-089's assignment, and its refusal to change once recorded is load-bearing — moving that value after content exists strands every artifact written under the old tenant, with correct answers and a silent re-payment for every parse as the only symptom. Its absence is also actively misleading, because it sits beside a bullet reading "**No tenants table.**" That bullet is still true in the sense it means — no per-tenant entity, no foreign key — and it now needs one clause saying so, or a reader comparing the document against `\dt` concludes the document is wrong. `docdoc_schema_version` is migration bookkeeping and a shorter entry is defensible; say which it is rather than leaving it out. T112 checks the `runs` table against the migration now, so consider whether the same check should assert that *every* `CREATE TABLE` in the migrations has a section, which is what would have caught this
- [X] T116 [P] Update R8's SQL block in `specs/009-asynchronous-runs/research.md` to the query that exists (contradicts). It shows `WHERE run_id = (SELECT … FOR UPDATE SKIP LOCKED LIMIT 1)` with no `attempts < %(max_attempts)s` bound, and the code has never matched it: T020 added the attempt bound FR-021 requires, and Phase 10 moved the candidate into a CTE so the `run.transition` event can name the state the run came *from* — without which a first claim and a redelivery are indistinguishable in the log. **The rationale is untouched and must stay**: one statement so there is no window between selecting and claiming, `SKIP LOCKED` so workers step over each other, `ORDER BY created_at` for FR-024, `now` as a parameter for FR-072. Only the block is stale. Add a line saying the CTE exists to carry `from_state`, so the next reader does not simplify it back and silently lose the distinction

**What this pass confirms rather than finds.** All five "deliberately absent" entries hold: no results
table, no tenants table in the sense meant, no retention sweep or `expired` state, no priority or
queue-name or scheduled-for column, no worker table. All thirteen Out-of-Scope exclusions hold. Every
acceptance scenario, edge case, invariant, transition rule and index is covered or asserted. Of the
seventeen research decisions, fourteen are honoured exactly; R8 and R13 are above, and **R9's "lease
90 s, heartbeat every 30 s, both configurable" is left alone deliberately** — the heartbeat is derived
as a third of the lease rather than configured separately, which is the better design, because two
independent knobs admit a heartbeat longer than the lease and that configuration loses runs. The
research note's phrasing is loose; the code is right, and changing the code to match the phrase would
be the wrong direction.

**`CHANGELOG.md`, for the fifth time.** Its `[Unreleased]` section describes Milestone 8 and says
nothing about Milestone 9, while Milestones 4 through 8 each wrote a full entry. No artifact in this
feature names it, so this command still appends no task for it. Recorded again so the omission stays
a decision.
