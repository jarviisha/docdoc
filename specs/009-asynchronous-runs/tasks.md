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
- [ ] T030 [P] [US1] Write `tests/contract/test_submission_is_immediate.py` — submission returns under 200 ms at p95 with the worker pool stopped, so no run can complete during measurement (SC-002)
- [X] T031 [P] [US1] Write `tests/integration/test_two_runs_one_result.py` — the same document twice yields two `run_id`s, one `processing_id`, and zero parser invocations on the second, counted rather than timed (FR-005)

### Implementation for User Story 1

- [X] T032 [US1] Implement the worker loop in `src/docdoc/runs/worker.py`: claim → heartbeat → `pipeline.run()` → `finish()`, calling the pipeline once and holding no copy of the stage order. **One run at a time**; the heartbeat timer is the only thread the process has (FR-025, R9a, contracts/runs-layer.md)
- [X] T033 [US1] Add `docdoc worker [--lease-seconds 90] [--max-attempts 3]` — **no `--concurrency` flag**, per FR-025 and R9a — in `src/docdoc/cli/commands/worker.py`, registered as a subcommand of the existing single console script, resolving schemas, adapters, and limits from the **same** configuration vocabulary the API uses with no second set of variable names (FR-041)
- [X] T034 [US1] Implement clean shutdown on `SIGTERM` in `src/docdoc/runs/worker.py`: stop claiming, then finish or `release()` so the run re-queues immediately rather than waiting out its lease (FR-042, FR-043)
- [X] T035 [US1] Add `POST /v1/documents/{blob_id}/runs` to `src/docdoc/api/app.py`, returning 202 with `run_id` and **omitting** `processing_id` rather than nulling it, per contracts/runs-http-api.md and ADR-0012 §3's precedent
- [X] T036 [US1] Add `GET /v1/runs/{run_id}` to `src/docdoc/api/app.py`, returning any of the five states, with 404 for unknown and for another tenant's alike
- [X] T037 [US1] Add `SubmissionResponse`/`RunResponse` models to `src/docdoc/api/models.py`, sharing a base with the existing run responses so they cannot drift in a field neither is defined to differ in
- [ ] T038 [US1] Wire run-state configuration into `src/docdoc/api/settings.py` — `DOCDOC_RUN_DATABASE_URL`, `DOCDOC_RUN_LEASE_SECONDS`, `DOCDOC_RUN_MAX_ATTEMPTS` — following the existing explicit-argument-over-environment-over-default precedence, and give each a flag unless it is a credential (FR-083)
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

- [ ] T047 [P] [US3] Write `tests/integration/test_shared_store_reuse.py` — two workers on one MinIO bucket; a document already parsed and resubmitted by the **same tenant** reuses the parse, counted on a parser invocation counter (SC-005)
- [ ] T048 [P] [US3] Write `tests/integration/test_redelivery.py` — kill the worker at each of the four stage boundaries; every run completes on redelivery with an identical `processing_id`, and billable invocations exceed the uninterrupted count by at most one, by zero between stages (SC-003, SC-004)
- [ ] T049 [P] [US3] Write `tests/integration/test_restart_survival.py` — restart the API and every worker mid-run; 100% of in-flight runs reach a terminal state with no operator action (SC-011)
- [ ] T050 [P] [US3] Write `tests/integration/test_s3_store_rules.py` — the six rows of contracts/runs-layer.md's table: miss, format mismatch, content mismatch raising, unreachable degrading, identical write as no-op, divergent write raising. Plus a store-equivalence case: the same document through the filesystem store and the S3 store agrees on every value, verdict, location, and identity (FR-051) — a cheap copy of SC-001 aimed at the store rather than the transport

### Implementation for User Story 3

- [ ] T051 [US3] Implement `S3ArtifactStore` in `src/docdoc/artifacts/s3.py` satisfying the existing `ArtifactStore` Protocol, with `boto3` imported lazily in the constructor so a base install neither imports nor requires it (R5)
- [ ] T052 [US3] Implement `S3BlobStore` in `src/docdoc/artifacts/s3.py` on the same terms
- [ ] T053 [US3] Ensure both stores in `src/docdoc/artifacts/s3.py` inherit ADR-0010 §4 and §5 rather than restating them — degradation, format mismatch, content mismatch, and the no-overwrite rule — sharing the filesystem store's logic where it is store-agnostic (R3, FR-047, FR-048)
- [ ] T054 [US3] Wire store selection into `src/docdoc/api/settings.py` via `DOCDOC_STORE_URL`, keeping the filesystem store the default and unchanged (FR-050)
- [ ] T055 [US3] Apply `tenant_root()` in both stores' key derivation in `src/docdoc/artifacts/s3.py` and in the filesystem store, so namespacing is on from the first commit and the default tenant resolves to the **unprefixed** legacy layout (FR-084, FR-084a, FR-088). No relocation, no copy, no read-through fallback
- [ ] T056 [P] [US3] Correct the `pyproject.toml` layers comment claiming `artifacts` "depends on `pydantic` and two kernel hashing helpers and on nothing else" — it stops being true in this change, and a comment that lies is worse than one that is absent (R5)

**Checkpoint**: multi-worker deployment is safe. The reuse guarantee holds across processes.

---

## Phase 6: User Story 4 — Deploy it behind a load balancer (Priority: P2)

**Goal**: Both process types can be scheduled and taken out of rotation correctly.

**Independent test**: Stop the database; liveness passes, readiness fails naming the dependency,
submission is refused retryably.

### Tests for User Story 4

- [ ] T057 [P] [US4] Write `tests/integration/test_health_endpoints.py` — with Postgres stopped: liveness 200 in 100% of probes, readiness 503 in 100% naming `run-state-database`, submission 503 retryable in 100% and accepted-then-lost in 0% (SC-009)
- [ ] T058 [P] [US4] Write `tests/unit/test_health_discloses_nothing.py` — neither route returns configuration values, credentials, tenant identifiers, or counts of stored content (FR-058); and probe readiness with adapter and parser counters attached, asserting both stay at zero, because a readiness check that bills the deployment is worse than none (FR-056)

### Implementation for User Story 4

- [ ] T059 [US4] Implement `GET /healthz` in `src/docdoc/api/health.py` returning a constant and touching no database, store, or provider (FR-053)
- [ ] T060 [US4] Implement `GET /readyz` in `src/docdoc/api/health.py`: one `SELECT 1`, one store metadata call against a fixed key, short timeout, two-second cache, naming the unmet dependency on failure (R13, FR-054, FR-055)
- [ ] T061 [US4] Make readiness **strict** in `src/docdoc/api/health.py` — a process that cannot reach the database reports not ready even though the synchronous routes still work (FR-087) — and state in the docstring that this withdraws working capacity on purpose
- [ ] T062 [US4] Refuse `POST /…/runs` in `src/docdoc/api/app.py` with a retryable 503 when the run-state database is unreachable, rather than accepting work that cannot be recorded (FR-057, gate 9)
- [ ] T063 [US4] Serve both routes from the worker process too via `src/docdoc/runs/worker.py`, on the same terms, so one orchestrator configuration covers both process types (FR-053, FR-054)
- [ ] T064 [US4] Register health routes in `src/docdoc/api/app.py` outside `/v1` and outside the authentication dependency (FR-058)

**Checkpoint**: the composition is schedulable by an orchestrator.

---

## Phase 7: User Story 5 — Keep one tenant out of another tenant's results (Priority: P2)

**Goal**: A shared deployment is safe for more than one customer.

**Independent test**: Two keys; each identifier — run, blob, processing — unreadable under the other.

### Tests for User Story 5

- [ ] T065 [P] [US5] Write `tests/contract/test_tenant_isolation.py` — cross-tenant access to `run_id`, `blob_id`, and `processing_id` returns responses **byte-identical** to those for a non-existent identifier, in status and body (SC-008)
- [ ] T066 [P] [US5] Write `tests/contract/test_no_existence_oracle.py` — a tenant submitting a document another tenant already processed invokes the parser and the model adapter exactly as many times as a first-ever submission (SC-017). **This is the test the namespacing decision exists for; a status code cannot satisfy it**
- [ ] T067 [P] [US5] Write `tests/integration/test_upgrade_compatibility.py` — seed a store in the Milestone 8 layout, then with authentication at its default assert previously stored blobs and artifacts stay readable **at their original paths**, that reuse still hits, and that existing routes return what they returned before; after enabling authentication, 0% of pre-existing content is unreachable and nothing was copied or moved (SC-018, FR-084a)
- [ ] T068 [P] [US5] Write `tests/unit/test_idempotency_scope.py` — one key twice under one tenant produces one run; the same key under two tenants produces two (SC-016)

### Implementation for User Story 5

- [ ] T069 [US5] Implement `src/docdoc/api/auth.py`: load a key file named by `DOCDOC_API_KEYS_FILE`, resolve a key to a principal carrying exactly one `tenant_id`, compare with `secrets.compare_digest` over a SHA-256 (R14, FR-060, FR-061)
- [ ] T070 [US5] Make authentication **disabled by default** in `src/docdoc/api/auth.py`, with one implicit tenant owning everything and behaviour identical to Milestone 8 (FR-088)
- [ ] T071 [US5] Validate `tenant_id` as `[a-z0-9_-]{1,64}` at the auth boundary in `src/docdoc/api/auth.py` so a value that could escape a path segment never reaches a store, and do not re-validate in the stores (R12)
- [ ] T072 [US5] Apply the authentication dependency in `src/docdoc/api/app.py` to every route except `/healthz` and `/readyz`, rejecting before any document is read, provider called, or store touched (FR-059, FR-067)
- [ ] T073 [US5] Scope blob reads and the existing job routes by tenant in `src/docdoc/api/app.py`, returning the non-existence response for another tenant's identifier (FR-064, FR-065, FR-066)
- [ ] T074 [US5] Assert in `tests/unit/test_credential_never_logged.py` that no credential reaches a log line, a run record, an error body, or a process argument list (FR-068) with a test over the seeded-string fixture
- [ ] T074a [P] [US5] Write `tests/unit/test_cli_and_library_need_no_credential.py` — with `DOCDOC_API_KEYS_FILE` set, every CLI subcommand and every in-process library entry point still runs with no credential supplied (FR-069). Authentication is an HTTP concern and this is the test that keeps it one
- [ ] T075 [US5] Add `src/docdoc/runs/migrations/0002_default_tenant.sql` plus the explicit, idempotent step that assigns pre-existing content to a configured default tenant, inferring no owner and leaving nothing unreachable (FR-089)

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

- [ ] T076 [US6] Add `should_continue: Callable[[], bool] | None = None` to `pipeline.run()` in `src/docdoc/pipeline/runner.py`, consulted **only at stage boundaries**, default preserving existing behaviour byte for byte (R4)
- [ ] T077 [P] [US6] Unit-test in `tests/unit/test_pipeline_cancellation.py` that `should_continue=None` produces results identical to before, and that returning `False` stops before the next stage while leaving completed stages' artifacts written and yielding no `processing_id`
- [ ] T078 [US6] Consult the cancellation flag from the worker loop in `src/docdoc/runs/worker.py` (contracts/runs-layer.md)
- [ ] T079 [US6] Add `DELETE /v1/runs/{run_id}` to `src/docdoc/api/app.py` — 200 for queued and running alike, 409 naming the state for terminal runs, 404 for another tenant's (FR-031, FR-063)
- [ ] T080 [P] [US6] Write `tests/integration/test_cancellation.py` — a queued run never executes; a running run stops before the *next* billable stage; 0% of in-flight provider calls are aborted (SC-015)
- [ ] T081 [US6] Document in contracts/runs-http-api.md and the concept doc that a 200 on a running run means *requested*, not *stopped*, and that a provider call already in flight completes and is billed (FR-029)

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T082 [P] Write `docs/concepts/runs.md` covering the two identities, the run lifecycle, redelivery, and the exact limits of cancellation (FR-079)
- [ ] T083 [P] Write `examples/submit_async_run.py` — submit and poll to completion, runnable against the composition with the `echo` adapter (FR-080)
- [ ] T084 [P] Update the README: roadmap row for Milestone 9, the Over HTTP section with the three run routes, and the configuration section with every new setting (FR-081)
- [ ] T085 Revise the README's unauthenticated warning to state what is now true — that authentication exists, is off by default, and a deployment which has not enabled it is exactly as exposed as before (FR-081). **The warning must not be deleted**; a deployment at the default is unchanged
- [ ] T086 [P] Update `examples/serve_api.md` to cover running a worker alongside the API
- [ ] T087 Verify SC-010: run `examples/evaluate_golden_set.py` and confirm golden-set metrics are **bit-identical** to the pre-milestone output. Any movement is evidence the scope escaped, not a regression to investigate
- [ ] T088 Verify SC-012: `git diff --stat` against the merge base touches **zero** files under `src/docdoc/kernel`, `ingest`, `extraction`, `grounding`, `validation`, and zero lines of the artifact envelope's identity derivation
- [ ] T089 Verify SC-013: a base install acquires zero new dependencies, and `uv run pytest tests/unit tests/property` passes with neither `docdoc[postgres]` nor `docdoc[s3]` installed and no database or bucket present
- [ ] T090 Walk `quickstart.md` end to end on a clean checkout and confirm the 10-minute claim (SC-014); correct the document where it is wrong rather than the timing where it is inconvenient
- [ ] T091 Flip `specs/009-asynchronous-runs/spec.md` Status from `Draft` to `Implemented` and update the README roadmap table in the same change, per the status vocabulary's wiring
- [ ] T092 [P] Run `uv run ruff check .`, `uv run mypy src/docdoc/runs`, and `uv run lint-imports`, and confirm `HYPOTHESIS_PROFILE=thorough uv run pytest` is green

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
