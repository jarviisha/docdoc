---

description: "Task list for Milestone 7 — pipeline, artifact store, CLI, and HTTP API"
---

# Tasks: Pipeline, Artifact Store, CLI, and HTTP API

**Input**: Design documents from `/specs/007-pipeline-api-cli/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/](contracts/), [quickstart.md](quickstart.md)

**Tests**: not optional here. The constitution's override applies to two areas this feature touches
directly — **layer boundaries** (four new layers, one amended principle) and **evaluation-affecting
changes** (the recorder is rewritten, so golden-set metrics must be reported). Beyond that, every
reuse claim in this milestone is unobservable without an instrument: a stale cache returns a result
that looks correct, so the tests here are not a quality ritual but the only way the feature can be
seen to work at all.

**Organization**: by user story, in priority order. Each story is a shippable increment.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US5, mapping to the user stories in [spec.md](spec.md)

## Path Conventions

Single project: `src/docdoc/`, `tests/` at repository root, per the Structure Decision in plan.md.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: create the four packages and make the layer boundary a build failure before any code
can cross it.

- [X] T001 Create package skeletons `src/docdoc/artifacts/__init__.py`, `src/docdoc/pipeline/__init__.py`, `src/docdoc/cli/__init__.py`, `src/docdoc/api/__init__.py`, each with a module docstring naming what the layer owns and what it must never import
- [X] T002 Extend the `import-linter` layers contract in `pyproject.toml` to `api, cli > recording > evaluation > pipeline > validation > grounding > extraction > ingest > artifacts > kernel`, with a comment explaining why `artifacts` sits directly above the kernel and `pipeline` directly above `validation` (research R1, FR-052)
- [X] T003 Add an `independence` contract between `docdoc.api` and `docdoc.cli` in `pyproject.toml`, since neither may import the other and a layer position would have implied a permission (FR-054, research R1)
- [X] T004 [P] Add `docdoc.artifacts` and `fastapi` to the existing forbidden-imports contracts for `docdoc.grounding` and `docdoc.validation` in `pyproject.toml`, so "these layers do no I/O and know no transport" stays a build failure (plan post-design re-check, gates 4 and 5)
- [X] T005 [P] Add the `api = ["fastapi>=0.110", "uvicorn>=0.29"]` extra to `pyproject.toml` with a comment stating it is confined to `docdoc.api` (research R13, FR-038)
- [X] T006 [P] Add `[project.scripts] docdoc = "docdoc.cli:main"` to `pyproject.toml`; no new runtime dependency, because the CLI is argparse (research R12, FR-053)
- [X] T007 Amend Principle X in `.specify/memory/constitution.md` to the chain T002 enforces, bump the document to 1.5.0, and record the amendment with its rationale and migration note — **in the same commit as T002**, which is what Principle X itself requires (FR-055)
- [X] T008 [P] Add a CI job to `.github/workflows/ci.yml` that installs the base package with no extras and runs the offline suite, so "the base install acquires nothing" is checked rather than asserted (FR-053, SC-013)
- [X] T009 Run `uv run lint-imports` and confirm the new contracts pass against the empty packages before any code is written

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: the store and the stage machine. Nothing in any user story can be built on top of these
until they exist and are proven.

**⚠️ CRITICAL**: no user story work begins until this phase is complete.

### Decisions that gate code

- [X] T010 Write `docs/adr/0010-artifact-store-and-job-model.md`: the on-disk layout, the artifact-format version and why it is not the package version, the synchronous job model, and the decision not to require a database or an object store while leaving room for one behind the same boundary — each a choice a later reader would otherwise reconstruct from code (FR-018, research R4, R6, R7)
- [X] T011 Accept or supersede ADR-0003's amendment of 2026-08-18 in `docs/adr/0003-content-addressed-artifact-chain.md`, which Milestone 5 wrote and left marked **proposed**, and update the constitution's decision table if it names it. **Blocks T028**: FR-058 requires folding exactly the inputs that amendment names, so building on it while it is unaccepted is the implicit resolution the constitution's precedence rule forbids (FR-065)

### Errors

- [X] T012 [P] Add `ArtifactError` to `src/docdoc/artifacts/errors.py` as a `DocdocError` subclass, carrying the store root, the artifact id, and the reason (FR-050)
- [X] T013 [P] Add `PipelineError` to `src/docdoc/pipeline/errors.py` as a `DocdocError` subclass (FR-050)

### The artifact store

- [X] T014 Implement `ArtifactEnvelope` in `src/docdoc/artifacts/envelope.py` with `artifact_id`, `stage`, `input_artifact_id`, `processor_id`, `processor_version`, `options_hash`, `artifact_format_version`, `content_id`, and `payload`. Document in the docstring why `content_id` exists: an `artifact_id` hashes a stage's *inputs*, so rehashing the payload against it would always fail, and corruption would be undetectable (research R4, FR-014, FR-022)
- [X] T015 Implement the `ArtifactStore` protocol and `NullArtifactStore` in `src/docdoc/artifacts/store.py` — content-addressed, immutable, append-only, per ADR-0003. The null store is the default and there is no default root, which is what makes FR-017 true by construction rather than by a flag being checked correctly at every call site (FR-011, FR-017)
- [X] T016 Implement `FileArtifactStore` in `src/docdoc/artifacts/store.py`: two-character hash fan-out, writes via a temporary file in the same directory followed by an atomic replace, and directories created readable only by the owning account (research R4, FR-016, FR-044)
- [X] T017 Implement `get()` in `src/docdoc/artifacts/store.py` with all three read outcomes — absent is a miss, an incompatible `artifact_format_version` is a **miss that is logged**, and a `content_id` mismatch **raises** (FR-014, FR-015)
- [X] T018 Implement `put()` in `src/docdoc/artifacts/store.py` with the conflict rule: a no-op when the stored content matches, and `ArtifactError` naming both contents when it does not. Never overwrite — this is what makes "append-only" a property rather than a description, and the only mechanical symptom available for a processor whose output moved while its version did not (FR-011, FR-062)
- [X] T019 Implement `clear(stage=None)` in `src/docdoc/artifacts/store.py` — all of it or one stage, and no query language (FR-019)
- [X] T020 Implement `BlobStore` in `src/docdoc/artifacts/blobs.py`: idempotent `put` keyed by `blob_id`, owner-only permissions, no envelope. Blobs are whole source documents and are the more sensitive of the two stores (FR-021, FR-044)
- [X] T021 Implement `DerivationRecord` and `derivation()` in `src/docdoc/artifacts/derivation.py`, carrying the stage, the input id, the processor and version, and the **names** of the folded inputs — no payload, no value, no prompt, no credential — and returning `None` when no record was written (FR-023, FR-025)

### The store, proven

- [X] T022 [P] Unit tests for envelope integrity in `tests/unit/test_artifact_envelope.py`: a mutated payload fails its `content_id`, and an `artifact_id` alone cannot detect it
- [X] T023 [P] Unit tests in `tests/unit/test_artifact_store.py` covering **every row** of the semantics table in `contracts/pipeline-api.md` §6, including the two conflict rows and the two degradation rows
- [X] T024 [P] Property test in `tests/property/test_artifact_store_properties.py`: `put` then `get` returns a model equal to the original, for each of `Document`, `ExtractionResult`, `GroundingResult`, and `ValidationResult`
- [X] T025 [P] Test in `tests/unit/test_artifact_store.py` that a partially written file is never readable as a complete artifact (FR-016)
- [X] T026 [P] Test in `tests/integration/test_store_concurrency.py` that two concurrent puts of identical content both succeed with no lock, and that divergent concurrent puts raise (FR-062)
- [X] T027 [P] Test in `tests/unit/test_artifact_store.py` that the store root and every directory it creates are not group- or world-readable (FR-044)

### The stage machine

- [X] T028 Implement per-stage processor identity and options-hash construction in `src/docdoc/pipeline/stages.py`, folding **exactly** the inputs ADR-0003 names for each stage and nothing else. Depends on T011 (FR-058)
- [X] T029 Implement `Stage`, `StageOutcome`, `PipelineResult`, and `RunProvenance` in `src/docdoc/pipeline/result.py` per `data-model.md`, with `failure_class` documented as the error's class name and never its message (FR-004)
- [X] T030 Implement `run()` in `src/docdoc/pipeline/run.py` — the four stages in order against `NullArtifactStore`, returning one `PipelineResult`, with `processing_id` set to the terminal artifact id (FR-001, FR-002, FR-006, FR-007, FR-008)
- [X] T031 Implement failure handling in `src/docdoc/pipeline/run.py`: a failed stage ends the run, every preceding result is kept, and the stage is attributed by the **declaring layer** of the error rather than by what was executing (FR-004, FR-005)
- [X] T032 [P] Test in `tests/unit/test_stage_identity.py` that each stage folds exactly the inputs ADR-0003 names — asserted input by input, by name — and that adding an unfolded input that changes a result is caught. Under-folding causes stale reuse and over-folding destroys reuse, and neither is visible in any output (FR-058)
- [X] T033 [P] Test in `tests/unit/test_stage_identity.py` that durations, `request_id`, retry counts, and transport settings never enter an identity (FR-060)
- [X] T034 [P] Test in `tests/unit/test_stage_identity.py` that computing every stage identity succeeds with no credentials, no network, and no provider configured (FR-059)
- [X] T035 [P] Integration test in `tests/integration/test_pipeline_failures.py` injecting a failure at each of the four stages and asserting the preceding results survive and the failing stage is named (FR-004, SC-012)
- [X] T036 [P] Test in `tests/unit/test_pipeline_errors.py` that no untyped exception escapes `run()` for any injected failure (FR-051, SC-011)
- [X] T107 [P] Test in `tests/unit/test_pipeline_errors.py` that a `ValidationError`, a `GroundingError`, and a `SchemaError` each cause exactly **one** stage execution, while a provider error is retried under the policy the extraction layer already owns. The pipeline adds no retry of its own, and nothing else asserts that (FR-010)
- [X] T037 Add the dependency-direction test in `tests/unit/test_layer_boundaries.py`, covering the new chain and the `api`/`cli` independence, so the boundary is checked by a test as well as by `lint-imports` (constitution override, FR-052, FR-054)

**Checkpoint**: the store is trustworthy and the pipeline runs end to end without reuse. User stories can begin.

---

## Phase 3: User Story 1 — Get an answer out of a PDF with one command (Priority: P1) 🎯 MVP

**Goal**: the project's Definition of Done — a PDF in one end of a command, and every value's page
and rectangle out the other.

**Independent Test**: run `docdoc inspect` against a committed fixture PDF and a committed schema
with no credentials and no network; confirm each field carries value, verdict, page, and bounding box.

- [X] T038 [US1] Implement `main()` and subcommand dispatch with argparse in `src/docdoc/cli/__init__.py`, with a global `--json` and the shared configuration flags (research R12)
- [X] T039 [P] [US1] Implement the `parse` command in `src/docdoc/cli/commands/parse.py`: route, parse, and report what the parse produced including the text-layer verdict
- [X] T040 [US1] Implement the `extract` command in `src/docdoc/cli/commands/extract.py`, calling `pipeline.run()` and nothing else (FR-030)
- [X] T041 [US1] Implement the `inspect` command in `src/docdoc/cli/commands/inspect.py`: per field, its value, its verdict, its page, and its bounding box — the half of the Definition of Done that answers *where did this come from*
- [X] T105 [US1] Implement the `eval` command in `src/docdoc/cli/commands/eval.py`, loading a golden set and a prediction set and rendering the report — the fifth of the five commands FR-026 names, and the one promised as `docdoc eval ./dataset` at the founding. It calls `docdoc.evaluation` and computes nothing of its own (FR-026, FR-030)
- [X] T106 [P] [US1] Test in `tests/integration/test_cli_offline.py` that `docdoc eval datasets/mvp/manifest.json --predictions datasets/mvp/predictions` reproduces exactly the numbers `examples/evaluate_golden_set.py` prints today, with no credentials and no network — the CLI is a front end, not a second implementation (FR-026, FR-029, quickstart §8)
- [X] T042 [US1] Implement rendering in `src/docdoc/cli/render.py`: a human form and a machine form, with the machine form the **only** thing on standard output and every diagnostic on standard error (FR-027)
- [X] T043 [US1] Implement the exit-code contract in `src/docdoc/cli/__init__.py` — `0` valid, `1` invalid, `2` could not run, `64` bad invocation — so a caller never greps text to tell a failed validation from a failed run (FR-028)
- [X] T044 [US1] Implement configuration precedence in `src/docdoc/cli/__init__.py`: explicit flag beats environment beats default, with `--schema-path` and `--adapter` mirroring the existing variable names and introducing no second vocabulary (FR-031)
- [X] T045 [US1] Make the unresolvable-schema error in `src/docdoc/cli/commands/extract.py` say the registry is **empty** and name the setting that fills it, rather than reporting that the schema does not exist (US1 scenario 5)
- [X] T046 [P] [US1] Contract test in `tests/contract/test_cli_contract.py` asserting every claim in `contracts/cli.md`: the command set, the two output forms, the four exit codes, and the stdout/stderr split — and that no untyped exception escapes the command for any failure injected at any stage (FR-051, SC-011)
- [X] T047 [P] [US1] Integration test in `tests/integration/test_cli_offline.py` running `parse`, `extract`, and `inspect` over `tests/fixtures/pdf/digital_invoice.pdf` with the echo adapter and `socket` patched to raise, asserting every value carries a page and a bounding box (FR-029, SC-001)
- [X] T048 [P] [US1] Test in `tests/integration/test_cli_offline.py` that a document failing validation exits `1` while a document that could not be processed exits `2`, and that the partial results survive in the second case (FR-028, SC-012)
- [X] T049 [P] [US1] Test in `tests/contract/test_cli_contract.py` that `--json` writes exactly one parseable JSON document to standard output with a warning emitted concurrently (FR-027)
- [X] T050 [US1] Add `examples/run_pipeline.py` demonstrating `pipeline.run()` in-process with no store and no credentials, and reference it from the README's example list (FR-056)

**Checkpoint**: the Definition of Done is reachable by anyone with a shell. This is a shippable MVP.

---

## Phase 4: User Story 2 — Change the prompt without paying for the parse again (Priority: P2)

**Goal**: ADR-0003's partial reuse, executing for the first time in the project's history.

**Independent Test**: run one document twice with per-stage counters; assert the second run executes
zero stages. Change only the prompt; assert the parse count is still zero while extraction runs.

- [X] T051 [US2] Split `parse()` in `src/docdoc/ingest/parse.py` into a planning call returning the routed verdict, the selected parser, and the canonical options with their hash, and an execution call that runs it. `parse()` keeps its exact signature and becomes the composition, so no existing caller changes (research R2, plan Complexity Tracking)
- [X] T052 [P] [US2] Test in `tests/unit/test_parse_plan.py` that the planning call yields everything `document_id_for` needs and that `plan` then `execute` produces a `Document` identical to today's `parse()` for every fixture in `tests/fixtures/pdf/`
- [X] T053 [US2] Wire the store into `src/docdoc/pipeline/run.py`: compute each stage's identity, look it up, and execute only on a miss, recording `EXECUTED` or `REUSED` per stage (FR-012, FR-004)
- [X] T054 [US2] Place the parse-stage lookup **after routing and selection and before the parser executes** in `src/docdoc/pipeline/run.py`, so a reused parse skips the parser — including a billable service-backed one — while the text-layer verdict is still computed and recorded on every run (FR-061, gate 6)
- [X] T055 [US2] Implement degradation in `src/docdoc/pipeline/run.py`: an unreadable, unwritable, or full store means the run proceeds without reuse and logs once, and a failed write never fails a run whose stages succeeded (FR-063)
- [X] T056 [US2] Implement `run(..., verify=True)` in `src/docdoc/pipeline/run.py` — execute every stage and still write, so FR-062's conflict check fires on results that would otherwise have been read back (FR-064)
- [X] T057 [US2] Add the bounded match-view cache to `src/docdoc/grounding/view.py`, keyed on `(document_id, match_view_version)` with least-recently-used eviction, a configurable maximum entry count, and a documented default. No filesystem access, so grounding's forbidden-imports contract still holds (FR-020, research R11)
- [X] T058 [P] [US2] Test in `tests/unit/test_match_view_cache.py` that a cached view produces outcomes identical to a freshly built one, that the bound is honoured, and that the cache is never reached when the grounding artifact itself is reused (FR-020)
- [X] T059 [US2] Add `--store`, `--no-store`, and `--verify-cache` to `src/docdoc/cli/__init__.py`, with no default store root — the store is opt-in because the artifacts hold extracted values and the blobs hold whole documents (FR-017, FR-044, FR-064)
- [X] T060 [US2] Implement `docdoc store clear [--stage STAGE]` in `src/docdoc/cli/commands/store.py`, which is the supported recovery path from a failed integrity check (FR-019)
- [X] T061 [US2] Add per-stage executed/reused counters to `src/docdoc/pipeline/observe.py` and surface them on `PipelineResult`, so the cost of a run is readable off the run (FR-047, SC-004)
- [X] T062 [P] [US2] Integration test in `tests/integration/test_reuse.py`: a second identical run executes zero stages and every result field matches the first **except** the per-stage durations and statuses (SC-002)
- [X] T063 [P] [US2] Integration test in `tests/integration/test_reuse.py`: after a change confined to the prompt or the schema, the parse executes zero times and every stage from extraction onward executes exactly once, counted from T061's counters and never from a timing. Repeat for a change confined to a validation rule, which must reuse the parse too — partial reuse is per-stage in both directions (FR-013, SC-003)
- [X] T064 [P] [US2] Integration test in `tests/integration/test_reuse.py` over a fixture store seeded with one corrupt artifact, one under an incompatible format version, and one conflicting write per stage: the corrupt one and the conflicting write raise, the incompatible one misses, and none is returned as a reusable result (SC-005)
- [X] T065 [P] [US2] Integration test in `tests/integration/test_reuse.py` that a fully reused run succeeds with **no credentials configured at all** (FR-059)
- [X] T066 [P] [US2] Integration test in `tests/integration/test_reuse.py` that a cached parse still computes and records the text-layer verdict, so a cached document never arrives carrying a routing decision this run did not make (FR-061, gate 6)

**Checkpoint**: reuse is real, partial, and provable. Iterating on a prompt no longer re-parses.

---

## Phase 5: User Story 3 — Call it over HTTP (Priority: P3)

**Goal**: the whole pipeline over the network, with no queue and no database.

**Independent Test**: submit, extract, poll, fetch; confirm the returned identity equals the one the
library computes for the same inputs.

- [X] T067 [US3] Implement request and response models in `src/docdoc/api/models.py`, with the submission response carrying `blob_id` and **not** a field named `document_id` — no parse has happened, and a blob id under that name anchors to nothing (research R8, FR-032)
- [X] T068 [US3] Implement `POST /v1/documents` and `GET /v1/documents/{blob_id}` in `src/docdoc/api/app.py`, storing bytes idempotently through `BlobStore` (FR-032, FR-021)
- [X] T069 [US3] Implement `POST /v1/documents/{blob_id}/extract` in `src/docdoc/api/app.py`, running the pipeline inside the request and returning the terminal artifact id as the job id **together with the result in full** — an identity-only response is unredeemable whenever no store is configured, which is the default (FR-033, FR-067, research R7)
- [X] T070 [US3] Implement `GET /v1/jobs/{job_id}` and `GET /v1/jobs/{job_id}/result` in `src/docdoc/api/app.py` as store lookups, with a closed status set of `succeeded`, `unavailable`, and `unknown` — **no pending**. `unknown` means the id is not a well-formed artifact identity; `unavailable` covers every well-formed absent id, cleared or never produced, because an append-only store with no tombstones cannot tell those apart (FR-035, FR-036)
- [X] T071 [US3] Implement the request-body size cap in `src/docdoc/api/app.py`, enforced while reading and before the body is buffered — the one limit `ingest.Limits` cannot know about (FR-039, research R10)
- [X] T072 [US3] Thread `ingest.Limits` through the API for document size and the media-type allowlist rather than introducing a second limits vocabulary, and ensure temporary files are removed on completion, failure, and abort (FR-039, FR-040, FR-041)
- [X] T073 [US3] Implement the error-to-status mapping in `src/docdoc/api/errors.py` per `contracts/http-api.md` §6, carrying docdoc's own message and never a provider's, which may quote the document (FR-037)
- [X] T074 [P] [US3] Contract test in `tests/contract/test_http_contract.py` asserting every endpoint, every status in the mapping table, and that a document failing validation is a `200` with an invalid verdict rather than an error — and that no untyped exception escapes any endpoint, every failure arriving as a status in that table (FR-051, SC-011)
- [X] T075 [P] [US3] Integration test in `tests/integration/test_http_parity.py` running the same inputs in-process and over HTTP and comparing every value, verdict, location, and identity (FR-034, SC-010)
- [X] T076 [P] [US3] Integration test in `tests/integration/test_http_limits.py` that an oversized or disallowed submission is refused with zero parses, zero provider calls, and zero surviving temporary files (SC-009)
- [X] T077 [P] [US3] Test in `tests/contract/test_http_contract.py` that a malformed job id is reported `unknown`, that a well-formed one — never produced or cleared alike — is reported `unavailable` and **not** silently recomputed, and that no input produces `pending` (FR-035, FR-036)
- [X] T108 [US3] Implement the partial-result error body in `src/docdoc/api/errors.py`: a mid-run failure returns the typed error **and** the per-stage outcomes and completed stages' results, so FR-004 survives the boundary. A failed run has no terminal artifact and therefore no job to fetch later, which makes this response the only place a partial result can appear (FR-066, FR-004)
- [X] T109 [P] [US3] Test in `tests/contract/test_http_contract.py` that a failure injected at each of stages 2, 3, and 4 returns the preceding stages' results in the error body, and that the same failure over the CLI and over HTTP agree on the stage, the error class, and which results survive (FR-066, SC-012, spec §Edge Cases)
- [X] T110 [US3] Refuse `POST /v1/documents` with an explicit error naming the missing setting when no store is configured, rather than accepting bytes that cannot be kept and returning an identity that will never resolve (FR-068, FR-021)
- [X] T078 [US3] Add `examples/serve_api.md` or a README section showing the service started behind the `docdoc[api]` extra, with a note that authentication is the deployment's responsibility (FR-056, spec Assumptions)

**Checkpoint**: the pipeline is reachable over the network and provably returns the same results.

---

## Phase 6: User Story 4 — Find out where an identity came from (Priority: P4)

**Goal**: the tool ADR-0003 accepted unreadable cache keys on the condition of.

**Independent Test**: take an identity produced by a run and ask for its derivation; confirm it names
the stage, the input id, the processor and version, and the folded input names, with no content.

- [X] T079 [US4] Implement `docdoc explain ARTIFACT_ID [--chain]` in `src/docdoc/cli/commands/explain.py`, reading `store.derivation()` (FR-023)
- [X] T080 [US4] Implement chain walking in `src/docdoc/cli/commands/explain.py`, following `input_artifact_id` back to the source `blob_id` (FR-024)
- [X] T081 [US4] Make an identity with no stored record say so plainly rather than reconstructing a derivation — a run with no store configured produces identities that were never recorded (FR-023, FR-017)
- [X] T082 [P] [US4] Test in `tests/unit/test_explain.py` that the output contains no document content, no extracted value, no prompt body, and no credential, using a fixture whose document holds a distinctive string (FR-025, SC-007)
- [X] T083 [P] [US4] Test in `tests/unit/test_explain.py` that the chain from a terminal identity reaches the source `blob_id` in four hops (FR-024)

**Checkpoint**: every cache decision is auditable in both directions.

---

## Phase 7: User Story 5 — See what a run did without seeing what the document said (Priority: P5)

**Goal**: operability without creating a second copy of the documents in the log store.

**Independent Test**: run a document containing known unique strings; assert none of them and no
credential appears anywhere in the logs, while every required field does.

- [X] T084 [US5] Implement the correlation context in `src/docdoc/pipeline/observe.py` carrying `request_id` and `processing_id`, threaded through the run without entering any identity (FR-045, FR-060)
- [X] T085 [US5] Emit one `pipeline.stage` event per stage from `src/docdoc/pipeline/observe.py` with `step_id`, duration, outcome, and `reused`, reusing the payload conventions the existing per-layer `observe.py` modules already established rather than introducing a second logging system (FR-045, FR-046, research R9)
- [X] T086 [US5] Carry the provider, model, and token usage into the stage event from the layer that called the provider, and record a reused stage as reused so a cost question is answerable from logs alone (FR-046)
- [X] T087 [US5] Add the tracing hook to `src/docdoc/pipeline/observe.py` — a callable, not a dependency — so a deployment can bridge to OpenTelemetry without the base install growing a tracing stack (FR-048, research R9)
- [X] T088 [P] [US5] Integration test in `tests/integration/test_no_leak.py` running a document containing distinctive strings with a credential configured, asserting that none of the strings, no extracted value, no credential, and no prompt body appears in **any of the six surfaces FR-042 names** — the captured log output, the returned `PipelineResult`, the artifacts written to the store, the error body of an injected failure, an `explain` derivation, and any computed identity — while every constitutionally required log field is present. One fixture and one distinctive string serve all six; splitting them would only duplicate the setup (FR-042, FR-043, SC-008)
- [X] T089 [P] [US5] Test in `tests/unit/test_pipeline_observe.py` that enabling and disabling observability changes no result, no identity, and no verdict (FR-049)

**Checkpoint**: every user story is complete and independently demonstrable.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T090 Rewrite `_record_one` in `src/docdoc/recording/record.py` to call `pipeline.run()` instead of sequencing the four stages itself, and delete the known-limitation paragraph from the module docstring, which this milestone has now fixed (FR-009)
- [X] T091 Keep the recorder storeless by default in `src/docdoc/recording/record.py`, so a committed prediction set is always the product of full execution and a stale artifact can never move a published metric (spec Assumptions)
- [X] T092 Test in `tests/integration/test_recorder_parity.py` that regenerating the committed public-tier prediction set produces **byte-identical** files, proving the stage sequence now exists in exactly one place in the repository (FR-009, SC-014)
- [X] T093 Report golden-set metrics through the CLI before and after this milestone — field accuracy, coverage, missing rate, incorrect rate, grounding rate — and confirm every number is unchanged. This milestone measures and reuses; it must not move a metric, and an unchanged metric is also the evidence that the pipeline reimplemented no stage's behaviour. Depends on T105 (FR-003, constitution gate 4, quality gate 5)
      → **Unchanged, and by a stronger measure than the five metrics.** Run on `main` (Milestone 6, `5e7bdc2`) in a worktree and on this branch through `docdoc eval`, the whole report's content-addressed `report_id` is identical: `sha256:c3b4bc8e686aaa25a710067857b6aff7dc90f20c679057cba229b992d88d2b8e`. Since `report_id` folds every outcome, every metric, and the provenance, a single moved field would move it. The five headline numbers agree as expected: field accuracy 26/28, coverage 24/25, missing 1/25, incorrect 1/25, grounding 30/30.
- [X] T094 Test in `tests/integration/test_eval_cost.py` that evaluating the committed golden set twice with the store enabled performs zero repeated parses — the cost the store exists to remove (SC-015)
- [X] T095 [P] Test in `tests/unit/test_identity_recompute.py` that a run's terminal identity is recomputable from `RunProvenance` and the recorded per-stage identities, versions, and options hashes, and from nothing else (SC-006)
- [X] T096 Write `docs/adr/0011-pre-1.0-versioning.md`: the `0.x` policy, what a minor and a patch bump promise, and the two surfaces that get a deprecation path rather than a silent break — the kernel's identity derivations and the on-disk artifact format (FR-057, research R14)
- [X] T097 Move `TODO(PRE_1_0_VERSIONING)` from *Still open* to *Resolved* in `.specify/memory/constitution.md`, in the same change as T096, and confirm the section then lists **zero** open decisions each pointing at an accepted ADR (FR-057, SC-016)
- [X] T098 [P] Write `docs/concepts/pipeline.md`: the four stages, the reuse decision, what a cached parse still pays for and what it skips, and why a job needs no queue (FR-056)
- [X] T099 [P] Write `docs/concepts/artifacts.md`: the chain, the two hashes and why both exist, the miss-versus-raise rules, and the conflicting-write check as the one symptom of a missed version bump (FR-056)
- [X] T100 [P] Add the CLI and HTTP sections to `README.md`, update the documentation list, and add the new concept docs and contracts to it
- [X] T101 Update the `README.md` roadmap: mark Milestone 7 **Done**, and flip `specs/007-pipeline-api-cli/spec.md` **Status** from `Accepted` to `Implemented` — the transition this repository wires to the roadmap task so the field cannot silently lie
- [X] T102 [P] Add a `CHANGELOG.md` entry for the milestone, naming the partial-reuse behaviour, the CLI, the HTTP interface, and the two resolved ADRs
- [X] T103 [P] Run every scenario in [quickstart.md](quickstart.md) end to end and correct any drift between it and the shipped commands
- [X] T104 Run the full gate — `HYPOTHESIS_PROFILE=thorough uv run pytest`, `uv run mypy src/docdoc/kernel`, `uv run ruff check .`, `uv run lint-imports`, and the coverage job — and confirm the 93% floor still holds with four new packages measured
      → **All green.** Coverage **94.20%** against the 93% floor with `artifacts`, `pipeline`, `cli`, and `api` measured. `mypy` clean on 13 kernel files, `ruff` clean, `lint-imports` 8 contracts kept / 0 broken, property suite green under `HYPOTHESIS_PROFILE=thorough`.
      → Note on how the gate is run: the perf suite gets **its own job** (`ci.yml` line 121, `pytest -m perf`) and the main suite excludes it (`-m "not perf"`). Running both together makes `test_construction_of_50k_tokens_is_under_300ms` flaky under CPU contention — it measures 160 ms standalone against a 300 ms budget, and the pre-Milestone-7 baseline measures the same, so the contention is the cause rather than any change here. Run the two commands separately, as CI does.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies. T002 and T007 must land in the same commit (FR-055).
- **Foundational (Phase 2)**: depends on Setup. **Blocks every user story.** T011 blocks T028, and T028 blocks everything that computes an identity.
- **US1 (Phase 3)**: depends on Foundational only. Ships without the store.
- **US2 (Phase 4)**: depends on Foundational. Independent of US1, though T059–T060 extend the CLI US1 builds.
- **US3 (Phase 5)**: depends on Foundational. Independent of US1 and US2; T075's parity test is stronger once US2 exists but does not require it.
- **US4 (Phase 6)**: depends on Foundational, and is only meaningful with a store, so in practice after US2.
- **US5 (Phase 7)**: depends on Foundational. Its `reused` field (T085) is only exercised once US2 lands.
- **Polish (Phase 8)**: T090–T094 depend on the pipeline (Phase 2) and, for T094, on US2. **T093 and T094 additionally depend on T105**, since both run the golden set *through the CLI*. T096–T097 depend on nothing and could land at any point.

### Numbering

Append-only. T105–T107 were added by the cross-artifact analysis pass and T108–T110 by the interface
checklist pass (2026-08-24), and both are placed in the phases they belong to rather than at the end,
so every pre-existing T-id still points at the same task. T069, T070, and T077 were *rewritten* rather
than supplemented, because the contract they cited changed under them.

### Within Each User Story

Models before services, services before commands and endpoints, and the test that proves the story
before the story is called done. For US2 specifically, T051 (the ingest split) precedes T053–T054,
because the lookup point does not exist until the seam does.

### Parallel Opportunities

- Phase 1: T004, T005, T006, T008 in parallel after T001.
- Phase 2: T012 and T013 in parallel; T022–T027 in parallel once T014–T021 land; T032–T036 and T107 in parallel once T028–T031 land.
- Phase 3: T039 in parallel with T040–T041 and T105; T046–T049 and T106 in parallel once the commands exist.
- Phase 4: T062–T066 in parallel once T053–T057 land.
- Phase 5: T074–T077 in parallel once T067–T073 land.
- Phases 3, 5, and 7 can be staffed in parallel by three people once Phase 2 is complete.

---

## Parallel Example: Phase 2, once the store lands

```bash
# The store's proof, all at once:
Task: "Unit tests for envelope integrity in tests/unit/test_artifact_envelope.py"
Task: "Unit tests for every row of the semantics table in tests/unit/test_artifact_store.py"
Task: "Property test for put/get round-trip in tests/property/test_artifact_store_properties.py"
Task: "Concurrency test in tests/integration/test_store_concurrency.py"
Task: "Permissions test in tests/unit/test_artifact_store.py"
```

---

## Implementation Strategy

### MVP first (User Story 1 only)

1. Phase 1: Setup — four packages, the layer contract, the constitution amendment.
2. Phase 2: Foundational — the store and the stage machine. This is the largest phase and the one
   everything rests on.
3. Phase 3: US1 — the command.
4. **Stop and validate**: quickstart scenario 1, offline, no credentials. This alone is the
   project's Definition of Done, stated at its founding and unreached for six milestones.

### Incremental delivery

US1 (the command) → US2 (reuse, and the end of re-parsing everything) → US3 (HTTP) → US4 (explain) →
US5 (observability) → Polish. Each is demonstrable on its own and none breaks the one before it.

### Notes

- `[P]` means a different file and no dependency on an incomplete task.
- Commit after each task or logical group.
- The reuse tasks are counted, never timed: a criterion that a slow machine can fail is not a
  criterion.
- Two tasks in Phase 8 close governance rather than code — T096 and T097 resolve the constitution's
  last open decision. This is the final milestone, and no later plan exists to carry them.

---

## Phase 9: Convergence

Appended by `/speckit-converge` on 2026-08-24, after Phase 1–8 were reported complete. Every item
below is a gap between what `spec.md`, `plan.md`, and the constitution require and what the
**repository** contains — which is not the same thing as what the working tree contains, and that
distinction is the first two tasks.

- [X] T111 **CRITICAL** — Anchor the `artifacts/` rule in `.gitignore` (line 77) so it matches only a store directory at the repository root, and commit the six untracked files of `src/docdoc/artifacts/`. The rule exists for a local content-addressed store and also matches `src/docdoc/artifacts/`, so the entire layer is untracked: `git ls-files src/docdoc/artifacts/` returns nothing, and commit `3a118b1` — whose message says "`docdoc.artifacts` is real" — contains that layer's four test files and none of its source. On a fresh clone `import docdoc.pipeline` raises `ModuleNotFoundError: No module named 'docdoc.artifacts'`, so the store, the blob store, the envelope, and the derivation record do not exist and nothing that rests on them can run. Use `/artifacts/` or a `!src/docdoc/artifacts/` negation rather than deleting the rule, which is still wanted for its original purpose per Constitution XII (FR-053, SC-013, missing)
- [X] T112 **CRITICAL** — Resolve `pydantic_core` in the kernel's import allowlist as a governance matter rather than a test edit. `tests/unit/test_kernel_purity.py` now permits it so that `SpanIndex.__get_pydantic_core_schema__` can exist, and the argument — that `pydantic_core` is pydantic's own compiled runtime rather than a second dependency, so Principle I's "only permitted runtime dependency is `pydantic`" still holds — is recorded in a comment in that test. The test's own failure message says adding a kernel dependency requires a constitution amendment. Either record the reasoning in `.specify/memory/constitution.md` under Principle I (a PATCH amendment, since it clarifies rather than widens) or remove the import and express the schema through `pydantic`'s public functional validators. Do not leave a constitutional guarantee argued only in a test comment (Constitution I, missing)
- [X] T113 Add a packaging test asserting that a built wheel contains every package under `src/docdoc/`. T111's defect survived a full green gate, `mypy`, `ruff`, `lint-imports`, and 94% coverage because every one of those ran against a working tree that had the files. The CI base-install job (T008) would have caught it and did not run on this branch. A test that builds the wheel and compares its package list against the source tree is local, fast, and fails for the right reason (FR-053, SC-013, missing)
- [X] T114 Add `tests/unit/test_parse_plan.py`: assert the planning call yields everything `document_id_for` needs, and that `plan_parse` then `execute_plan` produces a `Document` identical to `parse()` for **every** fixture in `tests/fixtures/pdf/`. T052 was marked complete without this file; `plan_parse` and `execute_plan` appear in the suite only as a monkeypatch target in `test_eval_cost.py`. The split is the seam every parse-reuse claim rests on, and nothing currently asserts the two halves compose back to the original (T052, FR-061, missing)
- [X] T115 Add `tests/unit/test_match_view_cache.py`: assert a cached view produces outcomes identical to a freshly built one, that the configured bound is honoured and evicts least-recently-used, and that the cache is never reached when the grounding artifact itself is reused. T058 was marked complete without this file. The cache is currently exercised only by two timing assertions in `tests/perf/test_grounding_perf.py`, which cannot see correctness — and the cache already had one real defect (a view returned for a document whose text differed) that only the full suite caught (T058, FR-020, missing)
- [X] T116 Emit the stage events for stages that ran before a `PipelineError` or `ArtifactError` propagates out of `run()`. `_emit` is called on the return path only, so a run that raises emits **zero** `pipeline.stage` events even though earlier stages executed — measured against a store with one corrupted artifact. FR-045 asks for one event per stage execution, and the runs worth having events for are disproportionately the ones that failed (FR-045, partial)
- [X] T117 Assert zero surviving temporary files after a request that is refused, fails, or is aborted. The HTTP layer reads bodies into memory and creates none, and the two stores unlink theirs — but nothing checks it, so SC-009's "leaves zero temporary files behind" is currently a property of the implementation rather than of the suite (SC-009, FR-041, missing)
- [X] T118 Add a permissions test for `BlobStore`: its root, its fan-out directories, and its files must not be group- or world-readable. `tests/unit/test_artifact_store.py` asserts this for the artifact store only. FR-044 names blobs explicitly *because* they are the more sensitive of the two — an artifact holds the values extracted from a document and a blob holds the document (FR-044, missing)
- [X] T119 Reconcile `quickstart.md` §7 with the shipped HTTP interface; they disagree three ways and the scenario cannot run as written. There is no `docdoc.api:app` attribute (the module exposes the `create_app` factory, deliberately, so importing it does not read the environment); submission reads a raw request body while the scenario posts multipart `-F file=@…`, which would store the identity of the multipart envelope rather than of the PDF; and `schema` is a query parameter while the scenario sends it as a JSON body, which would return 422. Decide which side moves — accepting multipart and a JSON body is defensible, and so is correcting the scenario — then make the other match. T103 was marked complete having exercised scenarios 1–6 only (quickstart §7, T103, partial)
- [X] T120 Decide whether the command line can read a stored result by identity, as `GET /v1/jobs/{id}/result` can. FR-026 requires a command to "inspect a result's values and their locations", and `inspect` takes a file and a schema and re-runs the pipeline — so somebody holding a `processing_id` from a log has an HTTP path and no CLI path. Either give `inspect` an identity form or record the asymmetry as intended in `contracts/cli.md`. This is `checklists/interfaces.md` CHK024, left open there (FR-026, partial)
- [X] T121 Make the maximum document size configurable outside code, or narrow FR-039 to say it is not. `ingest.Limits` carries the limit and reads no environment variable, so an operator running the command line or the service can configure the request cap (`DOCDOC_MAX_REQUEST_BYTES`) and not the document size. FR-039 requires both to be configurable; only one is, for anyone who is not importing the library (FR-039, partial)
- [X] T122 Review and justify or remove five additions no artifact asked for: the settings `DOCDOC_ECHO_FIXTURES`, `DOCDOC_MATCH_VIEW_CACHE`, and `DOCDOC_MAX_REQUEST_BYTES`, and the public functions `docdoc.validation.resolve_enabled_rules` and `docdoc.extraction.conform.retype`. Each was added to satisfy a requirement — the offline path of FR-029, the bound of FR-020, the request cap of FR-039, and the pre-stage identity computation of FR-012 — and none is named by `spec.md` or `plan.md`. Confirm each earns its place per Constitution XI's "every abstraction MUST have a concrete, present-tense reason to exist", and that the three settings belong in the configuration vocabulary FR-031 governs (Constitution XI, unrequested)
      → **All five kept, each traced to the requirement that forces it; the count is now seven.** Reviewed 2026-08-24.
      → `DOCDOC_ECHO_FIXTURES` — FR-029 requires the offline path to be runnable *from the command line*, and the echo adapter answers from files that have to come from somewhere. It is also the second of the two settings that must both be present before a fixture adapter can be selected at all, which is what keeps Milestone 3's "never a fallback" property intact.
      → `DOCDOC_MATCH_VIEW_CACHE` — FR-020 requires the bound to be "configurable and its default documented". Not optional.
      → `DOCDOC_MAX_REQUEST_BYTES` — FR-039 requires "a configurable request size limit", which is a different limit from the document size and the only one `ingest.Limits` cannot know about, because by the time bytes reach it they are already in memory.
      → `DOCDOC_MAX_DOCUMENT_BYTES` and `DOCDOC_MAX_PAGES` — added by T121 for the other half of FR-039. Two more than this task listed, and listed here so the count stays honest.
      → `validation.resolve_enabled_rules` — FR-012 requires a stage's identity before the stage runs, and the validate options hash folds the resolved rule set. The alternative was for the pipeline to restate the resolution, which is the "folded set in two places" failure FR-058 exists to prevent.
      → `conform.retype` — FR-012's "indistinguishable from the executed one" over a JSON round trip. `evaluation/values.py` had already met this problem and warned in its own docstring that a second copy of the coercion rules is how every decimal in a dataset starts reading as an extraction error; this adds a caller to `_coerce` rather than a third copy.
      → Every one of the seven is documented in `README.md`, which `tests/unit/test_documented_api_references_resolve.py` enforces in both directions — a setting the code reads and no document mentions fails, and so does the reverse.
- [X] T123 Correct four paths in Phases 2–7 that no longer locate the work they name: `src/docdoc/pipeline/run.py` is `runner.py` (deliberately — the module docstring explains that a module named `run` beside a function named `run` is shadowed by the package re-export), `tests/integration/test_http_parity.py` and `tests/integration/test_http_limits.py` are both inside `tests/contract/test_http_contract.py`, and `tests/unit/test_pipeline_observe.py` is inside `tests/integration/test_no_leak.py`. The assertions all exist; a reader following the task list to find them does not (tasks.md, partial)
      → **Recorded here rather than by editing the task lines**, because tasks.md is append-only and rewriting a completed task's path would erase the fact that it moved. The mapping, once:
      → `src/docdoc/pipeline/run.py` → `src/docdoc/pipeline/runner.py`. Deliberate: a module named `run` beside a re-exported function named `run` is shadowed by the package `__init__`, so `docdoc.pipeline.run` resolves to the function and anything reaching for the module by that path silently gets something else. The module docstring argues it, and `tests/integration/test_eval_cost.py` had to reach through `sys.modules` for exactly this reason.
      → `tests/integration/test_http_parity.py` (T075) → `tests/contract/test_http_contract.py::test_a_result_over_http_equals_the_same_run_in_process`.
      → `tests/integration/test_http_limits.py` (T076) → the same file's `test_an_oversized_request_is_refused_before_anything_is_parsed` and `test_a_disallowed_media_type_is_refused`.
      → `tests/unit/test_pipeline_observe.py` (T089) → `tests/integration/test_no_leak.py::test_observability_changes_no_result_no_identity_and_no_verdict`.
      → The three relocations share a reason: each assertion belongs beside the contract or the property it tests rather than in a file named after the task that asked for it.
