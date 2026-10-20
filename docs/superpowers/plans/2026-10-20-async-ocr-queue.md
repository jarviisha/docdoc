# Async OCR Through a Bounded Queue Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `POST /v1/ocr` stops OCRing in the request. It accepts the document into the existing
`runs` queue with `202`, refuses honestly when the consumer's cap (`429`) or the system cap (`503`)
is full, OCR workers process it, and the result is kept for 1 day by default.

**Architecture:** OCR becomes a second **run kind** on the existing `runs` table (`kind` column,
`schema_identity` nullable for OCR). The worker dispatches on kind and runs a parse-only path that
stores the `Document` as the parse-stage artifact, so retention and webhooks work unchanged. Caps
are counted live from `runs`. Workers can be restricted to one kind, and compose runs an OCR worker
separately.

**Tech Stack:** Python 3.11, FastAPI, psycopg/Postgres 16, SeaweedFS (S3), pytest, mypy, ruff, uv.

**Spec:** `docs/superpowers/specs/2026-10-19-async-ocr-queue-design.md`

## Global Constraints

- No new infrastructure: no Redis, broker, or workflow engine. The queue is the `runs` table.
- Run kinds are exactly `extraction` and `ocr`. Existing rows and callers default to `extraction`
  and behave as before.
- Per-consumer cap: default **100** OCR runs queued+running per tenant. Over it: **429** +
  `Retry-After: 30`.
- Global cap: default **500** OCR runs queued+running across all tenants. Over it: **503** +
  `Retry-After: 30`, body saying the system is overloaded.
- Both caps are **on with no configuration**.
- OCR retention: default **24 hours**, counted **from the run finishing**, swept even when
  `DOCDOC_RUN_RETENTION_DAYS` is unset. It never sweeps extraction runs when run retention is off.
- OCR runs count only toward the OCR caps. Extraction limits (`concurrent_runs`,
  `submissions_per_minute`, tokens) count only extraction runs.
- Every OCR request goes through the queue. There is no synchronous OCR path left.
- ADR-0015's difference rule stands: never delete an artifact or blob a surviving run names.
- ING-7: an OCR run either succeeds with one complete `Document` or fails. There are no partial
  results.
- Env names (new): `DOCDOC_OCR_QUEUE_PER_TENANT`, `DOCDOC_OCR_QUEUE_TOTAL`,
  `DOCDOC_OCR_RETENTION_HOURS`.
- Code must stay green under `uv run ruff check`, `uv run mypy src`, and
  `uv run pytest tests/unit tests/property tests/contract`. The Postgres tests run with
  `uv run pytest -m postgres` when `DOCDOC_TEST_DATABASE_URL` is set.

## Review Focus

1. **Postgres retention test:** the 1-day OCR sweep runs with run retention unset. An extraction run
   that is past its `expires_at` must survive. A blob shared by an expired OCR run and a live
   extraction run must survive. Pinned in Task 5.
2. **A redelivered OCR run** (lease lapsed after the parse artifact was written) must reuse the
   artifact and end succeeded exactly once. Pinned in Task 3 (`reused` outcome) and Task 4.
3. **Caps when the database is down.** If counting fails, submission answers 503 with
   `Retry-After` from the same transient branch `submit_run` uses. It never treats a failed count
   as zero. Pinned in Task 6.
4. **Requests refused before taking a slot:** an oversized body (413), an unknown parser (422) and
   an unsupported language (422) must not create a run or store a blob. Pinned in Task 6.
5. **An extraction-only worker must never claim an OCR run, and the reverse.** A deployment
   running the old single `docdoc worker` with no `--kind` still claims both. Pinned in Task 2
   and Task 4.

---

### Task 1: Run kind in the schema and the model

**Files:**
- Create: `src/docdoc/runs/migrations/0008_run_kind.sql`
- Modify: `src/docdoc/runs/model.py` (add `RunKind`, fields on `Run`)
- Modify: `src/docdoc/runs/queue.py` (`RunSpec` gains optional fields, read by `getattr`)
- Modify: `src/docdoc/runs/postgres.py` (`_COLUMNS`, `submit` INSERT)
- Modify: `tests/fixtures/run_queue.py` (`submit` honours the new fields)
- Modify: `src/docdoc/api/models.py` (`RunStateResponse.schema_identity: str | None`, add `kind: str`, `ocr_parser: str | None`, `ocr_language: str | None`)
- Test: `tests/unit/test_run_kind.py`, `tests/integration/test_run_kind_postgres.py` (marker `postgres`)

**Interfaces:**
- Produces: `class RunKind(StrEnum): EXTRACTION = "extraction"; OCR = "ocr"` in `docdoc.runs.model`, exported in `__all__`.
- Produces: `Run.kind: RunKind = RunKind.EXTRACTION`, `Run.schema_identity: str | None`, `Run.ocr_parser: str | None = None`, `Run.ocr_language: str | None = None`. A model validator requires `schema_identity` exactly when `kind is EXTRACTION`, and allows `ocr_*` only when `kind is OCR`.
- Produces: `RunSpec` may carry `kind`, `ocr_parser`, `ocr_language`. `submit` reads them with `getattr(spec, "kind", "extraction")` and the same pattern for the others, as it already does for `priority`.

- [ ] **Step 1: Write failing unit tests** in `tests/unit/test_run_kind.py`:
  - `test_extraction_run_requires_schema`: `Run(..., kind="extraction", schema_identity=None)` raises `ValidationError`.
  - `test_ocr_run_has_no_schema`: `Run(..., kind="ocr", schema_identity=None, ocr_parser="paddle", ocr_language="vie")` validates. With `schema_identity="x@1"` it raises.
  - `test_default_kind_is_extraction`: a `Run` built without `kind` has `kind is RunKind.EXTRACTION`.
  - `test_in_memory_submit_carries_kind`: `InMemoryRunQueue().submit(spec_with(kind="ocr", schema_identity=None, ocr_parser="paddle"), ...)` returns a run with `kind == "ocr"` and `ocr_parser == "paddle"`.
  - `test_dump_public_includes_kind`: `run.dump_public()["kind"] == "ocr"`.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_run_kind.py -v`. Expected: FAIL (`RunKind` not defined).
- [ ] **Step 3: Write `0008_run_kind.sql`**, idempotent like 0001–0007:
  - `ADD COLUMN IF NOT EXISTS kind text NOT NULL DEFAULT 'extraction' CHECK (kind IN ('extraction','ocr'))`
  - `ocr_parser text` and `ocr_language text`
  - `ALTER COLUMN schema_identity DROP NOT NULL`
  - constraint `schema_belongs_to_extraction CHECK ((kind = 'extraction') = (schema_identity IS NOT NULL))`
  - partial index `runs_active_by_kind ON runs (kind, tenant_id) WHERE status IN ('queued','running')`
- [ ] **Step 4: Implement the model, `RunSpec`, the Postgres `_COLUMNS`/`submit` changes, and the fake's `submit`.** The comment above `RunStatus` says the five-state set is closed. `kind` is a separate axis and changes nothing there. Every mypy error from `schema_identity` becoming optional is fixed at its call site: extraction-only paths `assert run.schema_identity is not None` after the kind check from Task 4. Do not widen those call sites.
- [ ] **Step 5: Add the Postgres test** `test_migration_0008_keeps_existing_rows_extraction`: apply migrations, insert a run through `submit` with a Milestone-9-shaped spec (no `kind`), and read it back as `kind == "extraction"`. Inserting an `ocr` row with a `schema_identity` violates `schema_belongs_to_extraction`.
- [ ] **Step 6: Run** `uv run pytest tests/unit tests/contract -q && uv run mypy src`. Expected: PASS. Run `uv run pytest -m postgres tests/integration/test_run_kind_postgres.py` too if a database is available.
- [ ] **Step 7: Commit** `feat(014): runs carry a kind; OCR runs have no schema`.

### Task 2: The queue learns kinds — claim filter, live counts, re-anchored expiry

**Files:**
- Modify: `src/docdoc/runs/queue.py`, `src/docdoc/runs/postgres.py`, `tests/fixtures/run_queue.py`
- Modify: `src/docdoc/runs/limits.py` (`_concurrency_check` counts `kind = 'extraction'` only)
- Test: `tests/unit/test_queue_kinds.py`, `tests/integration/test_queue_kinds_postgres.py` (marker `postgres`)

**Interfaces:**
- Produces: `RunQueue.claim(..., kind: RunKind | None = None)`. `None` claims any kind, as today. The Postgres filter is `AND (%(kind)s::text IS NULL OR runs.kind = %(kind)s)` on the `candidate` scan. The abandonment sweep stays kind-agnostic.
- Produces: `RunQueue.count_active(*, kind: RunKind, tenant_id: str | None = None) -> int`, which counts `status IN ('queued','running')`. `tenant_id=None` counts all tenants. It raises `RunStateUnavailableError` when unreachable, like every other method.
- Produces: `RunQueue.expiring(..., kind: RunKind | None = None)`. `None` returns any kind, as today.
- Produces: `finish` re-anchors expiry for OCR runs in the same UPDATE: `expires_at = CASE WHEN runs.kind = 'ocr' THEN %(now)s + (runs.expires_at - runs.created_at) ELSE runs.expires_at END`. The retention period chosen at submission therefore counts from finishing. Extraction runs are unchanged.

- [ ] **Step 1: Write failing tests** in `tests/unit/test_queue_kinds.py`, run against `InMemoryRunQueue`:
  - `test_claim_kind_filters`: queue one OCR run and one extraction run. `claim(kind=RunKind.OCR)` returns the OCR run, then `None`. `claim(kind=None)` returns the extraction one.
  - `test_count_active_by_kind_and_tenant`: tenant `a` has 2 queued OCR runs and 1 running OCR run, tenant `b` has 1 queued OCR run, and there is 1 extraction run. `count_active(kind=OCR, tenant_id="a") == 3`, `count_active(kind=OCR) == 4`, and a succeeded OCR run is not counted.
  - `test_ocr_expiry_counts_from_finish`: submit at T with `expires_at = T+24h` and finish at T+30h. Then `run.expires_at == T+54h`. An extraction run finished at the same moment keeps its `expires_at`.
  - `test_expiring_kind_filter`: given expired runs of both kinds, `expiring(kind=OCR)` returns only the OCR ones.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_queue_kinds.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** on the protocol, in Postgres and in the fake. The fake must enforce the same rules: it is a faithful fake, as its docstring says.
- [ ] **Step 4: Change the limiter.** `PostgresLimiter._concurrency_check` adds `AND kind = 'extraction'`. Test `test_concurrent_runs_ignores_ocr` (postgres): a tenant with `concurrent_runs: 1` and one queued OCR run can still submit an extraction run.
- [ ] **Step 5: Mirror Step 1 in `tests/integration/test_queue_kinds_postgres.py`** against `PostgresRunQueue`, plus `test_concurrent_runs_ignores_ocr`.
- [ ] **Step 6: Run** `uv run pytest tests/unit tests/contract -q && uv run mypy src`, plus the postgres file if available. Expected: PASS.
- [ ] **Step 7: Commit** `feat(014): claim by kind, count active runs, OCR expiry from finish`.

### Task 3: A parse-only pipeline entry

**Files:**
- Modify: `src/docdoc/pipeline/runner.py`, `src/docdoc/pipeline/__init__.py` (export)
- Test: `tests/unit/test_parse_only.py`

**Interfaces:**
- Produces: `docdoc.pipeline.parse_only(source: SourceFile | bytes, *, store: ArtifactStore | None, limits: Limits | None = None, options: Mapping[str, Any] | None = None, parser: str | None = None, registry: ParserRegistry | None = None, request_id: str | None = None) -> tuple[Document, StageOutcome]`.
- It runs the same parse block `_run` uses: `_plan_parse` → `_Reuse.get(document_id)` → `_execute_parse` → `_Reuse.put(... stage="parse", input_artifact_id=plan.file.blob_id, ...)`, with the same `_executed`/`_reused` outcome. **Extract that block into one private helper `_parse_stage(...)` that both `_run` and `parse_only` call**, so there is one parse-and-cache path. `_plan_parse` gains a `registry` passthrough to `plan_parse`.
- Parse errors propagate as their typed exceptions (`ParserCapabilityError`, `ProviderError`, …). `ArtifactError` behaves as in `_Reuse`: degrade on unavailable, raise on corrupt.

- [ ] **Step 1: Write failing tests** with a `FileArtifactStore` in `tmp_path` and a fake-post `PaddleXParser` registry, following the pattern in `tests/contract/test_ocr_routes.py`:
  - `test_parse_only_stores_document`: the returned outcome has `status == executed` and `artifact_id == document.id`, and `store.get(document.id, model=Document, ...)` returns an equal document.
  - `test_parse_only_reuses`: a second call with the same bytes and options gives `status == reused`, and the fake post is called once in total.
  - `test_parse_only_named_unknown_parser_raises`: `parser="nope"` raises `ParserCapabilityError`.
  - `test_pipeline_run_unchanged`: the existing `tests/contract/test_async_matches_sync.py` still passes. Run it in Step 4.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_parse_only.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** `_parse_stage` and `parse_only`.
- [ ] **Step 4: Run** `uv run pytest tests/unit tests/contract -q`. Expected: PASS.
- [ ] **Step 5: Commit** `feat(014): parse_only, sharing the pipeline's parse-and-cache path`.

### Task 4: The worker executes OCR runs, and can be restricted to a kind

**Files:**
- Modify: `src/docdoc/runs/worker.py` (`execute_one` dispatch, `_execute_ocr`, `Worker(kind=...)`, `Worker(parsers=...)`)
- Modify: `src/docdoc/cli/commands/worker.py` (`--kind {ocr,extraction}`)
- Modify: `packaging/docker/compose.yml` (split into `worker` with `--kind extraction` and `ocr-worker` with `--kind ocr`, same image, env and health check)
- Test: `tests/unit/test_worker_ocr.py`, extend `tests/contract/test_cli_contract.py`

**Interfaces:**
- Consumes: `parse_only` (Task 3), `claim(kind=...)` (Task 2), `Run.kind/ocr_parser/ocr_language` (Task 1).
- Produces: `execute_one(...)` gains `parsers: ParserRegistry | None = None`. When `run.kind is RunKind.OCR` it calls `_execute_ocr(run, queue=, blobs=, store=, parsers=, limits=, now=)` and returns `None`. The extraction path is unchanged: it starts with `assert run.schema_identity is not None`.
- `_execute_ocr`, in order:
  1. A missing blob finishes `FAILED`/`UnknownBlob`, as extraction does.
  2. `parse_only(data, store=store, limits=limits, options={"language": run.ocr_language} if set else {}, parser=run.ocr_parser, registry=parsers)`.
  3. On success, `_demand_the_result_is_retrievable`, then `finish(SUCCEEDED, processing_id=document.id, stage_outcomes=(record,))`.
  4. On a typed ingest error, `finish(FAILED, failed_stage="parse", error_class=type(exc).__name__)`.
  5. `ArtifactError(reason="unavailable")` and `RunStateUnavailableError` propagate, so the existing loop handlers redeliver.
  6. No tokens are recorded.
- Produces: `Worker(..., kind: RunKind | None = None, parsers: ParserRegistry | None = None)`. `kind` is passed to every `claim`. `docdoc worker --kind ocr|extraction`, absent meaning any kind.

- [ ] **Step 1: Write failing tests** in `tests/unit/test_worker_ocr.py`, using `InMemoryRunQueue`, a `FileArtifactStore`/`BlobStore` in `tmp_path`, and a fake-post parser registry:
  - `test_ocr_run_succeeds_with_document_id`: submit an OCR run over a stored PNG and call `execute_one`. The run is `succeeded`, `processing_id` is the stored `Document.id`, and `stage_outcomes[0].stage == "parse"`.
  - `test_ocr_run_provider_error_fails_at_parse`: the fake post raises `ProviderError`. The run is `failed` with `failed_stage == "parse"` and `error_class == "ProviderError"`.
  - `test_ocr_run_missing_blob`: the run is `failed` with `UnknownBlob`.
  - `test_ocr_redelivery_reuses_artifact`: run `parse_only` once to populate the store, then `execute_one` on a freshly claimed run. The outcome status is `reused`, the post is called once in total, and the run is `succeeded`.
  - `test_worker_kind_filter`: `Worker(kind=RunKind.EXTRACTION, ...)` never claims a queued OCR run. Drive `_claim_forever` with `stop()` after one idle tick, or test the `claim` kwargs through a spy queue.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_worker_ocr.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement** the worker changes and the CLI flag. The CLI passes `kind` and leaves `parsers=None`: the worker then uses `default_registry()`, as extraction already does through `ingest.parse`. Validating the endpoints file at startup already happens.
- [ ] **Step 4: Change compose.** Rename nothing operators rely on: `worker` stays and gets `command: ["docdoc", "worker", "--kind", "extraction", "--health-port", "8000"]`. Add an `ocr-worker` service with the same anchors and `--kind ocr`. Check that `tests/contract/test_milestone_9_deployment_unchanged.py` still passes, and adjust only what it asserts about the worker command, with a comment saying why.
- [ ] **Step 5: Add a CLI contract case**: `docdoc worker --kind bogus` exits with usage error code 2.
- [ ] **Step 6: Run** `uv run pytest tests/unit tests/contract -q && uv run mypy src && uv run ruff check`. Expected: PASS.
- [ ] **Step 7: Commit** `feat(014): workers execute OCR runs; --kind separates OCR from extraction`.

### Task 5: OCR retention, on by default

**Files:**
- Modify: `src/docdoc/runs/identity.py` (`OCR_RETENTION_HOURS_ENV = "DOCDOC_OCR_RETENTION_HOURS"`, `DEFAULT_OCR_RETENTION = timedelta(hours=24)`, `configured_ocr_retention(explicit: int | None = None) -> timedelta`)
- Modify: `src/docdoc/runs/retention.py` (`sweep(..., kind: RunKind | None = None)` passes `kind` to both `expiring` calls)
- Modify: `src/docdoc/runs/maintenance.py` (`tick`: when `retention_period is None`, sweep with `kind=RunKind.OCR` instead of returning. When set, sweep any kind, as today.)
- Modify: `src/docdoc/api/settings.py` (re-export the env name beside `RUN_RETENTION_DAYS_ENV`)
- Test: `tests/unit/test_ocr_retention.py`, `tests/integration/test_ocr_retention_postgres.py` (marker `postgres`)

**Interfaces:**
- Produces: `configured_ocr_retention()`: explicit, then env, then 24 h. A non-positive value is refused with the same error `configured_retention` raises. There is no "off" value. To disable it, set a large number. Note that in the env docstring.
- Consumes: `expiring(kind=...)`, re-anchored expiry (Task 2). The API stamps `expires_at = deadline(now, configured_ocr_retention())` in Task 6.

- [ ] **Step 1: Write failing tests** with `InMemoryRunQueue`:
  - `test_tick_sweeps_ocr_with_run_retention_off`: one OCR run finished 25 h ago, one extraction run past its `expires_at`. `tick(retention_period=None)` removes the OCR run (tombstone policy `retention`) and leaves the extraction run.
  - `test_tick_keeps_fresh_ocr`: an OCR run finished 23 h ago survives.
  - `test_shared_blob_survives`: an expired OCR run and a live extraction run name the same `blob_id`. After the sweep the blob is still in the `BlobStore`.
  - `test_configured_ocr_retention_default_and_env`: the default is 24 h, `DOCDOC_OCR_RETENTION_HOURS=2` gives 2 h, and `0` raises.
- [ ] **Step 2: Run** `uv run pytest tests/unit/test_ocr_retention.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement.** Update the `tick` docstring: it currently says it does nothing when retention is off. OCR sweeping is the stated exception (ADR-0022, Task 7).
- [ ] **Step 4: Mirror the first three tests against Postgres** in the integration file.
- [ ] **Step 5: Run** `uv run pytest tests/unit tests/contract -q && uv run mypy src`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(014): OCR runs are swept a day after finishing, by default`.

### Task 6: `POST /v1/ocr` accepts into the queue, with two caps; `GET /v1/runs/{id}/result`

**Files:**
- Modify: `src/docdoc/api/app.py` (the `ocr` route, `_RunSpec` slots, new `run_result` route, `_Deployment` caps)
- Modify: `src/docdoc/api/settings.py` (`OCR_QUEUE_PER_TENANT_ENV`, `OCR_QUEUE_TOTAL_ENV`, defaults 100 and 500)
- Modify: `src/docdoc/runs/limits.py` (`LimitPolicy.ocr_queue: int | None`, accepted in the limits file under `default` and `tenants.<id>`, used only as a per-tenant override)
- Modify: `tests/contract/test_ocr_routes.py` (synchronous assertions become async ones)
- Test: `tests/contract/test_ocr_queue.py`

**Interfaces:**
- Consumes: `count_active` (Task 2), `RunSpec` kind fields (Task 1), `configured_ocr_retention` (Task 5), `ingest.parse.plan_parse`.
- Produces: `_Deployment(..., ocr_queue_per_tenant: int | None = None, ocr_queue_total: int | None = None)`. `None` means read the env, then the default. The effective per-tenant cap is `limits book ocr_queue override for the tenant` if present, else the deployment value.
- Produces: `POST /v1/ocr?parser=&language=&callback_id=` → `202` with `RunStateResponse` (`kind: "ocr"`). Honours the `Idempotency-Key` and `X-Request-Id` headers like `submit_run`. `callback_id` is resolved with `_requested_callback`.
- Order of checks. Each refusal leaves no run and no blob:
  1. `has_runs` → otherwise 503 with no `Retry-After`. Reuse `submit_run`'s body. `has_store` → otherwise `_no_store_configured()`.
  2. `_read_capped` (413).
  3. `SourceFile.from_bytes` + `check_limits` (413/415).
  4. `plan_parse(file, options=..., registry=deployment.parser_registry(), parser=parser, limits=...)`. It catches an unknown or incapable parser and an unsupported language as 422 through the existing error mapping, without running OCR.
  5. Global cap: `count_active(kind=OCR) >= total` → 503, `Retry-After: 30`, body `{"error": "system_overloaded", "limit": "ocr_queue_total", "retry_after_seconds": 30, "message": "the system is overloaded; please send again later"}`.
  6. Tenant cap: `count_active(kind=OCR, tenant_id=tenant) >= cap` → 429, `Retry-After: 30`, body `{"error": "limit_exceeded", "limit": "ocr_queue", "observed": n, "allowed": cap, "retry_after_seconds": 30}`. This matches the existing 429 shape.
  7. `RunStateUnavailableError` from either count → the same transient 503 + `Retry-After` branch `submit_run` uses.
  8. `blobs.put(data)`, then `submit(spec(kind="ocr", schema_identity=None, ocr_parser=parser, ocr_language=language), expires_at=deadline(now, configured_ocr_retention()))`.
- `# ponytail: count-then-insert, so N concurrent submissions can overshoot a cap by up to N-1; add an advisory lock if that margin ever matters.`
- Produces: `GET /v1/runs/{run_id}/result`, tenant-scoped exactly like `run_state` (unknown → `_unknown_run`, tombstone → 410).
  - Extraction run → 409 `{"error": "not_an_ocr_run", "detail": "use GET /v1/jobs/{processing_id}/result"}`.
  - OCR run not succeeded → 409 `{"error": "run_not_succeeded", "status": ...}`.
  - Succeeded → `200` with the stored `Document` JSON, read from the tenant's store by `processing_id`.
  - Artifact missing → 410 `{"error": "result_expired"}`.

- [ ] **Step 1: Write failing tests** in `tests/contract/test_ocr_queue.py`, using `_Deployment(runs=InMemoryRunQueue(), store=..., blobs=..., parsers=<fake-post registry>, ocr_queue_per_tenant=2, ocr_queue_total=3)` and `TestClient`:
  - `test_submit_returns_202_without_ocr`: the fake post is **not** called, the response has `status == "queued"` and `kind == "ocr"`, and the blob is stored.
  - `test_tenant_cap_429`: two accepted, the third gets 429 with `Retry-After: 30` and `limit == "ocr_queue"`.
  - `test_global_cap_503`: with keys enabled, tenants `a` and `b` together fill 3. The next gets 503 with `Retry-After` and `error == "system_overloaded"`.
  - `test_one_tenant_full_does_not_refuse_another`: total 10, tenant `a` at its cap of 2, and tenant `b` is accepted.
  - `test_refusals_store_nothing`: an unknown parser (422), an oversized body (413) and a cap refusal all leave the blob store and the queue unchanged.
  - `test_count_unavailable_is_transient_503`: a queue whose `count_active` raises `RunStateUnavailableError` gives 503 with `Retry-After`.
  - `test_no_database_503`: with `runs=None`, the route answers 503 with no `Retry-After`.
  - `test_finished_slot_frees_cap`: after finishing one run, a new submission is accepted.
  - `test_result_route`: run `execute_one` (Task 4) on the queued run. `GET /v1/runs/{id}/result` gives 200 with `text` equal to the fake's text. Before execution it gives 409 `run_not_succeeded`. An extraction run gives 409 `not_an_ocr_run`. Another tenant's run gives the `_unknown_run` 404.
  - `test_limits_file_override`: a limits file with `tenants.bulk.ocr_queue = 5` lets `bulk` queue 5 while the default stays 2.
- [ ] **Step 2: Run** `uv run pytest tests/contract/test_ocr_queue.py -v`. Expected: FAIL.
- [ ] **Step 3: Implement the route, the settings, `LimitPolicy.ocr_queue` and `run_result`.** Rewrite the `ocr` docstring: it currently says it stores nothing, which is no longer true. Cite ADR-0022.
- [ ] **Step 4: Update `tests/contract/test_ocr_routes.py`.** Keep its secret-leak and 422 cases: a 422 still comes back synchronously from `plan_parse`. The 502 provider-error case moves to the worker: assert the run fails with `ProviderError` and that no secret appears in the run's `error_class`, the logs or the response.
- [ ] **Step 5: Run** `uv run pytest tests/unit tests/property tests/contract -q && uv run mypy src && uv run ruff check`. Expected: PASS.
- [ ] **Step 6: Commit** `feat(014): /v1/ocr queues with per-consumer and global caps; run result route`.

### Task 7: ADR-0022, docs, and an end-to-end check

**Files:**
- Create: `docs/adr/0022-asynchronous-ocr-through-a-bounded-queue.md`
- Modify: `docs/adr/README.md` (index), `README.md` (OCR section: 202 flow, caps, retention, `ocr-worker`), `docs/adr/0021-caller-named-parsers-and-ocr-endpoints.md` (one "Amended by ADR-0022" line), `docs/superpowers/specs/2026-10-19-ocr-gateway-design.md` (one line under Non-goals pointing to the new spec)
- Modify: `examples/submit_async_run.py` only if it is the documented client example. Otherwise add nothing.

**ADR-0022 must record:**
- run kinds
- removal of synchronous OCR (breaking: OCR now needs a database and a store)
- the two caps, with 429 vs 503
- OCR retention on by default and counted from finishing (an exception to "off unless configured")
- separate workers by kind (not priority)
- the cap overshoot margin

- [ ] **Step 1: Write the ADR and the doc edits.**
- [ ] **Step 2: End-to-end check** with the composition, if Docker is available: `docker compose -f packaging/docker/compose.yml up -d --build`, then `docdoc migrate`, following the README.
  1. `curl -X POST --data-binary @tests/fixtures/image/sample_page.png localhost:8000/v1/ocr?parser=<configured>` gives 202.
  2. Polling `GET /v1/runs/{id}` reaches `succeeded`.
  3. `GET /v1/runs/{id}/result` gives a `Document`.
  4. Submitting with `DOCDOC_OCR_QUEUE_PER_TENANT=1` and `ocr-worker` stopped gives 429 on the second request.

  If no OCR endpoint is reachable, say so and rely on the contract tests.
- [ ] **Step 3: Run the full suite:** `uv run pytest -q && uv run mypy src && uv run ruff check`. Expected: PASS, with the reported skips only.
- [ ] **Step 4: Commit** `docs(014): ADR-0022 and docs for async OCR`.
