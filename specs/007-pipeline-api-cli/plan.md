# Implementation Plan: Pipeline, Artifact Store, CLI, and HTTP API

**Branch**: `007-pipeline-api-cli` | **Date**: 2026-08-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/007-pipeline-api-cli/spec.md`

## Summary

Four new packages, in dependency order: `docdoc.artifacts` (a content-addressed, append-only store
that knows nothing about what it stores), `docdoc.pipeline` (the four stages as explicit, versioned
steps, plus the reuse decision), `docdoc.cli` (argparse, no new dependency, shipped in the base
install), and `docdoc.api` (an HTTP interface behind a new `docdoc[api]` extra).

Two findings from Phase 0 shape everything else.

**The parse stage's identity is knowable before the parse, but only just.** `document_id` is
`f(blob_id, parser_id, parser_version, options_hash)`, and `parser_id` comes from routing, which
reads the file to assess its text layer. So a cache lookup that skips the billable cloud parse has
to happen *after* routing and selection and *before* the parser runs — a seam `docdoc.ingest.parse()`
does not currently expose. Ingest therefore grows a plan/execute split. This is the only change to
an existing layer's public surface in the milestone, and it is what makes ADR-0003's partial reuse
real rather than aspirational.

**A synchronous job model needs no queue, no database, and no second identifier.** ADR-0003 already
says the job's `processing_id` is the terminal artifact id. Because the run completes inside the
request, that id exists by the time there is anything to hand back, and `GET /v1/jobs/{id}` is a
store lookup. A failed run produces no terminal artifact and therefore no job — it produces a typed
error, in the same request.

The rest is mostly reuse. Every layer already emits a structured log event through its own
`observe.py`; the pipeline adds correlation and a reused/executed flag rather than a second logging
system. `ingest.Limits` already enforces the size cap and the media-type allowlist before any parse
or transmission; the HTTP layer adds only a request-body cap. `kernel.content_id_for` and
`canonical_json` already supply the store's integrity check.

## Technical Context

**Language/Version**: Python 3.11+ (CPython 3.11 and 3.12, as CI already covers)

**Primary Dependencies**: `pydantic` and `rapidfuzz` (unchanged base install). `argparse` from the
standard library for the CLI — so the base install acquires **no new runtime dependency at all**.
`fastapi` and `uvicorn` behind a new `docdoc[api]` extra. No OpenTelemetry dependency; the pipeline
exposes an observer hook a deployment can bridge in a few lines.

**Storage**: Local filesystem, content-addressed and append-only, rooted at a configured directory.
No database, no object store, no migration. The store is optional: with none configured, every run
recomputes and every result is identical.

**Testing**: `pytest` (unit, property, contract, integration), `hypothesis`, `mypy --strict` for the
kernel, `ruff`, `import-linter` for the layer contract. New here: stage-execution counters as the
verification instrument for every reuse claim, a log-scanning test for the leakage prohibition, and
a byte-comparison of the regenerated golden prediction set for the recorder rewrite.

**Target Platform**: Linux and macOS; a single process. The HTTP interface is a single-node service.

**Project Type**: A library that gains a command-line front end and an optional HTTP front end.

**Performance Goals**: A second identical run executes zero stages. A prompt or schema change
executes zero parses. A cached parse pays the local text-layer assessment and skips the parser,
including the billable cloud call. These are stated as counts, not timings, so they are verified by
counting.

**Constraints**: The offline path — digital PDF, echo adapter, ground, validate, evaluate — runs with
no credentials and no network. No queue, no worker, no background execution, no database. Document
content, extracted values, credentials, and prompt bodies never reach a log. Caching must change
performance and nothing else: a result that differs because it was cached is a bug, not a trade-off.

**Scale/Scope**: One document per run, synchronous, single node. The largest corpus in the
repository is the 48-document public golden set, which is also the milestone's cost benchmark
(SC-015). Four new packages; one existing public function split in two; one existing package
(`docdoc.recording`) rewritten to call the pipeline instead of sequencing stages itself.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against constitution **v1.4.0** — the version current at planning. T007 amends Principle X
and bumps the document to 1.5.0 as part of this milestone, so a later reader should not take this
line as the current version. Note on gate 11: the template's gate text quotes the
pre-1.4.0 layer chain, which named `Transform` and `Pipeline` and omitted `grounding` and
`validation`. The authoritative chain is the `import-linter` contract in `pyproject.toml`, as
Principle X now says itself; the gate is answered against that.

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** — no new kernel dependency beyond `pydantic`; `Document` stays immutable; bytes stay in `BlobRef` | **PASS** — the kernel is not modified. The store reuses `content_id_for` and `canonical_json`, both already public. |
| 2 | **Provenance preservation (I, VIII)** — no operation discards spans, geometry, or provenance | **PASS** — the store round-trips whole result models and verifies their content id on read; `PipelineResult` records every stage's processor identity, version, options hash, and artifact id. Nothing is summarised on the way in or out. |
| 3 | **Grounding integrity (II)** — grounding computed by docdoc, never self-certified | **PASS** — grounding behaviour is untouched. The match-view cache is in-process memoisation on `(document_id, match_view_version)`, and a cached view that produced a different outcome would fail the reuse-equality tests. |
| 4 | **Determinism (III)** — no clock, randomness, network, or provider state in kernel/grounding/validation paths | **PASS** — the pipeline reads a monotonic clock for durations only, and no duration enters an identity, an artifact, or a verdict. All filesystem access lives in `docdoc.artifacts`, which the deterministic layers do not import. |
| 5 | **Provider isolation (IV)** — no provider SDK outside an adapter; base install pulls no provider SDK | **PASS** — the new packages import no provider SDK. `fastapi` is confined to `docdoc.api` behind an extra and is added to the forbidden-imports contracts of the deterministic layers. |
| 6 | **Text-first (V)** — OCR not forced on text-bearing documents; the text-layer decision stays inspectable | **PASS** — and this is load-bearing for the design: the cache lookup happens *after* routing, so the text-layer verdict is computed and recorded on every run, cached or not. |
| 7 | **Schema-driven (VI)** — no document-type-specific code path | **PASS** — the pipeline takes a schema identity as data and branches on nothing. The CLI and HTTP interface pass it through as a string. |
| 8 | **Validation separation (VII)** — semantic rules are deterministic code, not prompt instructions | **PASS** — validation is called, not reimplemented. |
| 9 | **No silent fallback (VIII)** — missing capability or provider failure raises a typed error | **PASS** — with one distinction designed deliberately and recorded in research R7: an artifact written under an incompatible format version is a **miss** and is logged as one, because a format bump is an expected event on upgrade; an artifact whose stored content does not match its recorded content id **raises**, because that is corruption and recomputing over it would hide it. |
| 10 | **Measurability (IX)** — quality claims backed by golden-set metrics | **PASS** — SC-015 runs the committed golden set through the CLI, and SC-014 proves the recorder rewrite by regenerating the committed prediction set and comparing bytes. |
| 11 | **Layer direction (X)** — dependencies flow downward; domain model free of HTTP/CLI/ORM/SDK | **PASS** — four layers inserted into the enforced contract (research R1), `api` and `cli` held apart by an independence contract, and Principle X amended in the same change (FR-055), which is what that principle requires. |
| 12 | **MVP discipline (XI)** — nothing from the Deferred Technology list; every abstraction justified | **PASS** — no queue, broker, worker, DAG engine, database, or object store. Two abstractions are argued rather than assumed and are recorded in Complexity Tracking below. |
| 13 | **Kernel test rigor (XII)** — kernel/span/geometry changes ship property tests | **N/A** — no kernel, span, or geometry change. The existing property suite gates this work as it gates everything. |
| 14 | **Open decisions** — no BLOCKING TODO gating this milestone is resolved implicitly in code | **PASS** — the opposite: `TODO(PRE_1_0_VERSIONING)`, the last open decision, is resolved *explicitly* by ADR-0011 with the constitution's table updated in the same change (FR-057, SC-016). |

### Post-design re-check (after Phase 1)

Re-evaluated against the design in `research.md`, `data-model.md`, and `contracts/`. No gate changed
status, and the design turned three of them from assertions into things CI can check:

- **Gate 4** — `docdoc.artifacts` is the only new package that touches a filesystem, and the
  deterministic layers must not reach it. Their existing forbidden-imports contracts gain
  `docdoc.artifacts` alongside `socket` and `httpx`, so "grounding does no I/O" stays a build failure
  rather than a claim.
- **Gate 5** — `fastapi` joins the same forbidden lists, which is where the constitution's own
  wording about the domain model staying free of it becomes enforceable.
- **Gate 11** — the `independence` contract between `docdoc.api` and `docdoc.cli` is what stops the
  HTTP layer from reusing the CLI's renderer, which would be the first coupling between two
  presentation layers with different audiences.

One design decision is worth flagging to a reviewer even though it passes: the parse-stage cache
lookup happens **after** routing, which means a cached run still assesses the text layer. That is
deliberate and it is what keeps gate 6 true — the text-layer verdict is computed and recorded on
every run, so a cached document never arrives with an unexplained routing decision.

## Project Structure

### Documentation (this feature)

```text
specs/007-pipeline-api-cli/
├── plan.md              # This file
├── research.md          # Phase 0 output — fourteen decisions
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── contracts/
│   ├── pipeline-api.md  # the Python surface of docdoc.pipeline and docdoc.artifacts
│   ├── cli.md           # commands, output forms, exit codes
│   └── http-api.md      # endpoints, statuses, error shapes
├── checklists/
│   └── requirements.md  # spec quality checklist (complete)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
src/docdoc/
├── kernel/              # unchanged
├── artifacts/           # NEW — content-addressed store, knows nothing of what it stores
│   ├── __init__.py
│   ├── envelope.py      # ArtifactEnvelope: payload + format version + content id
│   ├── store.py         # ArtifactStore protocol + FileArtifactStore + NullArtifactStore
│   ├── blobs.py         # BlobStore for submitted source bytes
│   ├── derivation.py    # DerivationRecord — how an id was derived (ADR-0003)
│   ├── paths.py         # owner-only directories, per level (FR-044)
│   └── errors.py        # ArtifactError
├── ingest/
│   ├── parse.py         # CHANGED — plan/execute split; parse() keeps its signature
│   └── ...              # otherwise unchanged
├── extraction/          # unchanged
├── grounding/
│   ├── view.py          # CHANGED — bounded in-process view cache (ADR-0006)
│   └── ...
├── validation/          # unchanged
├── pipeline/            # NEW — the four stages as one identified, versioned processor
│   ├── __init__.py      # run()
│   ├── stages.py        # Stage enum, per-stage processor identity and options hash
│   ├── runner.py        # the reuse decision and the stage loop
│   ├── plan.py          # each stage's identity, computed BEFORE the stage runs
│   ├── result.py        # PipelineResult, StageOutcome, RunProvenance
│   ├── observe.py       # correlation ids, stage events, counters
│   └── errors.py        # PipelineError
├── evaluation/          # unchanged
├── recording/
│   └── record.py        # CHANGED — calls pipeline.run(); outputs byte-identical
├── cli/                 # NEW — argparse; no new dependency; in the base install
│   ├── __init__.py      # main()
│   ├── commands/        # parse, extract, inspect, explain, eval, store
│   └── render.py        # the human form and the machine form
└── api/                 # NEW — behind docdoc[api]
    ├── __init__.py
    ├── app.py           # routes only
    ├── models.py        # request/response shapes
    ├── settings.py      # configuration NAMES, importable without FastAPI
    └── errors.py        # docdoc error -> status mapping

tests/
├── unit/
│   ├── test_artifact_envelope.py     # the two hashes, and why one cannot do the job
│   ├── test_artifact_store.py        # every row of contracts/pipeline-api.md §6
│   ├── test_stage_identity.py        # FR-058, input by input, per stage
│   ├── test_pipeline_errors.py       # FR-004, FR-005, FR-010, FR-051
│   ├── test_layer_boundaries.py      # the chain, and that the constitution agrees
│   ├── test_match_view_cache.py      # FR-020 (US2)
│   ├── test_explain.py               # FR-024, FR-025 (US4)
│   ├── test_parse_plan.py            # the ingest plan/execute split (US2)
│   ├── test_identity_recompute.py    # SC-006
│   └── test_packaging.py             # git and the wheel, not the working tree
├── property/
│   └── test_artifact_store_properties.py   # put/get round-trip, hash stability
├── contract/
│   ├── test_cli_contract.py          # contracts/cli.md
│   └── test_http_contract.py         # contracts/http-api.md
└── integration/
    ├── test_store_concurrency.py     # ADR-0010 §5, no lock required
    ├── test_pipeline_failures.py     # SC-012, real failures rather than injected ones
    ├── test_cli_offline.py           # SC-001, no credentials and no network
    ├── test_reuse.py                 # SC-002, SC-003, SC-005, FR-059, FR-061
    ├── test_no_leak.py               # SC-008, FR-042's surfaces, FR-045 on the raise path
    ├── test_recorder_parity.py       # SC-014
    └── test_eval_cost.py             # SC-015
```

**Structure Decision**: Four new top-level packages under `src/docdoc/`, inserted into the enforced
layer contract as `api, cli > recording > evaluation > pipeline > validation > … > ingest >
artifacts > kernel`. `artifacts` sits directly above the kernel because it depends on nothing else;
`pipeline` sits directly above `validation` because that is the highest stage it drives, which
leaves the existing `recording > evaluation` ordering untouched. `api` and `cli` are siblings at the
top, kept apart by an independence contract so neither can grow a dependency on the other.

### Four paths this block named that do not exist

Corrected on 2026-08-24, after two convergence passes found the tree describing files nobody wrote.
Recorded rather than silently edited, because three of the four moved for a reason worth keeping.

**`pipeline/run.py` is `pipeline/runner.py`.** A module named `run` beside a function named `run`
re-exported from the package `__init__` is shadowed by that re-export: `docdoc.pipeline.run` resolves
to the function, and anything reaching for the module by that path silently gets something else.
`tests/integration/test_eval_cost.py` has to reach through `sys.modules` for the same reason on
`docdoc.ingest.parse`, which has the identical collision.

**`pipeline/plan.py` was not planned and is load-bearing.** Each stage's identity has to be computed
*before* the stage runs, or the store can only record and never reuse — and no single layer could own
that, because it composes four layers' own `options_hash_for_*` functions.

**Three test files hold their assertions somewhere else.** `test_http_parity.py` (SC-010) and
`test_http_limits.py` (SC-009) are inside `tests/contract/test_http_contract.py`, beside the contract
they check; `test_pipeline_observe.py` (FR-049) is inside `tests/integration/test_no_leak.py`, beside
the prohibition it shares a fixture with. Each assertion belongs next to the property it tests rather
than in a file named after the task that asked for it.

**Two source files this block never named** were added by the convergence passes and are listed
above: `artifacts/paths.py`, which applies a directory mode per level because
`mkdir(parents=True, mode=…)` applies it to the leaf only — which had left both store roots
world-readable against FR-044 — and `api/settings.py`, which holds the HTTP interface's configuration
*names* outside the module that imports FastAPI, so that the check asserting every documented setting
exists can run on a base install with no extras.

## Complexity Tracking

Two decisions add structure and are argued here rather than assumed.

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `docdoc.artifacts` as its own layer rather than a `store.py` module inside `docdoc.pipeline` | The store must be unable to import stage code. If it deserialised results by importing `ExtractionResult`, the store would depend on four layers and every future stage would widen it; instead the caller passes the model class, and the store's dependencies are `pydantic` and the kernel's two hashing helpers. | As a module inside `pipeline`, nothing would prevent that import from being added later. This repository's own history is the argument: the layer chain stayed true for six milestones because a machine checks it, and drifted in the constitution's prose — the document nobody could compile — for three. A boundary that matters gets a contract. |
| Splitting `ingest.parse()` into a plan step and an execute step | `document_id` needs `parser_id` and `parser_version`, which routing decides by reading the file. Without a seam between "which parser, under which options" and "run it", the pipeline can either re-implement routing — which FR-003 forbids and which would put the text-layer decision in two places — or never skip a parse, which is the billable one on the cloud path. | A second cache key computed before routing was rejected: it would have to fold which parsers are *installed*, and an identity that changes when an optional dependency is added is not an identity. `parse()` keeps its current signature and behaviour and becomes a thin composition of the two halves, so no existing caller changes. |
