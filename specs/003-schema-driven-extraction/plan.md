# Implementation Plan: Schema-Driven Extraction

**Branch**: `003-schema-driven-extraction` | **Date**: 2026-08-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/003-schema-driven-extraction/spec.md`

## Summary

Build docdoc's L3 extraction layer: the thing that turns a Milestone 1 `Document` plus a versioned
schema into structured values. Four pieces of machinery keep it honest — a registry of schemas that are
*data*, an identity pair (`name@version` for the contract, `schema_hash` for the cache) fixed by ADR-0008,
one model adapter behind a contract that never names a provider in application code, and a provenance
record that makes any single result explainable a year later.

The layer is deliberately narrow. It owns the request, the conformance check, identity, and error
translation; it owns no judgment about whether an extracted value is *good*. It does not verify that a
value appears in the document — not even by exact search, which the kernel could already do — because
ADR-0003 makes grounding its own stage with its own artifact. It does not enforce the schema's
constraints, because Principle VII gives that to Milestone 5 and, as it turns out, because the provider
could not enforce them anyway (research.md R3).

What the layer does insist on is that the model's answer be *provably* the requested shape before it
becomes a result: the shape is enforced by the provider, checked again locally against a compiled model,
and a mismatch names the field rather than being coerced into place.

There is **no kernel change** at this milestone.

## Technical Context

**Language/Version**: Python >= 3.11 (unchanged from Milestones 1 and 2)

**Primary Dependencies**: Base install unchanged — `pydantic` only. One new optional extra:
`docdoc[anthropic]` → the official `anthropic` SDK ([research.md R1](research.md)). Stdlib used by the
layer: `hashlib`, `json`, `logging`, `time` (monotonic deadlines), `dataclasses`, `typing`.

**Storage**: N/A — `Document` + schema identity in, `ExtractionResult` out. No persistence, no artifact
store, no cache. This feature *derives* the artifact identity a cache will key on and stores nothing.

**Testing**: `pytest` + `hypothesis`, `mypy --strict`, `ruff`, `import-linter`. Three tiers
(research.md R11): offline unit/property/contract tests against the in-repo `echo` adapter; adapter tests
against recorded, scrubbed provider responses; live tests behind the existing `provider` marker.

**Target Platform**: Cross-platform library; CPython 3.11+ on Linux, macOS, Windows. Everything except
the model call itself must behave identically on all three.

**Project Type**: Single Python library, `src/` layout, published to PyPI

**Performance Goals** — the deterministic work only, per the spec's clarification. The model call is a
property of the provider and is *recorded* per extraction rather than bounded here. Enforced by
`tests/perf/test_extraction_perf.py` (marked `perf`).

| Operation | Target | Basis |
|---|---|---|
| Schema load + compile, per schema | < 200 ms | Once per schema at registration, not per extraction |
| Resolve + build request + conformance check + identity, 20-page doc / 20-field schema | < 100 ms | SC-021 |
| `schema_hash` over a 100-field schema | < 10 ms | Canonical JSON + one `sha256` |
| Input-budget guard | negligible | A character count (research.md R5) |
| Model call | recorded, not targeted | Dominated by the provider; governed by `deadline_s` |

The targets sit far above the expected measurements on purpose, for the reason Milestone 2 recorded: a
perf test that trips on machine noise gets disabled, and a disabled test protects nothing. What these
catch is an accidental per-extraction schema recompile, not constant-factor drift.

**Constraints**: The whole layer except the model call must run with no credentials and no network
(FR-044). Provider SDK types confined to `docdoc/extraction/adapters/` and enforced by `import-linter`
(FR-023). Transport settings must not influence artifact identity (FR-027) — true by construction, since
they live in a separate type (research.md R9). No clock, randomness, or I/O may be introduced into
`docdoc/kernel/`; the extraction layer may use all three. Document text, extracted values, claimed source
text, prompt content, and credentials never reach logs (FR-039).

**Scale/Scope**: Documents up to the configured input budget; schemas up to a few hundred fields.
Roughly 11 new modules plus one adapter and the in-repo `echo` adapter, no kernel change, and a fixture
set of 2 schemas, 2 prompts, and 3 recorded provider responses.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against constitution v1.2.0. **Initial: PASS. Post-design re-check: PASS.**

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** | **PASS** — no kernel change at all at this milestone (research.md R9 rejects promoting `ProviderError` for want of a present-tense need). `Document` is read and never modified (FR-009). `tests/unit/test_kernel_purity.py` must keep passing untouched |
| 2 | **Provenance preservation (I, VIII)** | **PASS** — every result records document identity, schema identity **and** `schema_hash`, `prompt_hash`, model id and version, decoding settings, adapter id and version, extractor version, token usage, and the derived artifact id (data-model §8). Re-extraction produces a new result; nothing is overwritten (FR-038) |
| 3 | **Grounding integrity (II)** | **PASS by deliberate abstention** — every value carries the ADR-0004 grounding fields and this feature leaves all of them unresolved (FR-032, FR-047). What it *does* supply is the byte-faithful claimed source text (FR-003) that Milestone 4 will resolve. Nothing is self-certified as grounded, and `model_confidence` is stored untrusted and routes nothing |
| 4 | **Determinism (III)** | **PASS** — the deterministic core (schema resolution, request assembly, conformance check, identity) has no clock, randomness, or network. The model is the probabilistic edge and is declared as such: FR-037 refuses to claim byte-identical repeats, which research.md R4 shows is the only honest position — the provider has no temperature and no seed to pin |
| 5 | **Provider isolation (IV)** | **PASS** — SDK imports confined to one adapter module and enforced by a new forbidden-imports contract; provider exceptions translated with `__cause__` preserved; base install pulls no provider SDK (SC-013). A content refusal — which arrives as HTTP 200, not an exception — is translated at the same boundary (research.md R12) |
| 6 | **Text-first (V)** | **N/A** — no parsing and no recognition at this milestone. This feature consumes whatever `Document` ingest produced and cannot re-route it |
| 7 | **Schema-driven (VI)** | **PASS** — this gate is the feature. Schemas and prompts are data files (FR-049); a second document type is added in the test suite with zero engine changes (SC-014), asserted by a check that fails the build; every result names the exact `name@version` and its content hash |
| 8 | **Validation separation (VII)** | **PASS** — extraction checks response shape and type parseability only (FR-006). Field constraints are *stored* in the schema and hashed into its identity, and deliberately not acted on; cross-field rules do not exist here. research.md R3 shows this split is forced by the provider's schema subset as well as required by the principle |
| 9 | **No silent fallback (VIII)** | **PASS** — an unknown schema identity names the requested identity and the available versions (FR-016); a missing credential names the unavailable capability before any byte is transmitted (FR-041); a failed model call never switches model, provider, or schema version (FR-029); an over-budget document is refused rather than truncated (FR-030) |
| 10 | **Measurability (IX)** | **PASS** — all 21 success criteria are countable. Extraction *accuracy* is Milestone 6's golden-set question and is not claimed here; what is measured here is conformance, identity, provenance completeness, and boundary containment |
| 11 | **Layer direction (X)** | **PASS** — `docdoc.extraction` sits above `docdoc.ingest`, which is the constitution's own order, and is added to the `import-linter` layers contract in the same change. Only two names are imported from ingest and both are justified in research.md R9 |
| 12 | **MVP discipline (XI)** | **PASS** — nothing from the Deferred Technology list. No cache, no queue, no vector store, no second adapter, no schema-authoring tool. Repetition is bounded to one level rather than left open (FR-048), and every new type traces to a spec requirement in data-model.md |
| 13 | **Kernel test rigor (XII)** | **PASS** — no span, geometry, or kernel operation semantics change, so the Milestone 1 property suite applies unchanged and must stay green. New property tests cover the canonical-form/hash relationship: field reordering must not move `schema_hash`, and any field/type/constraint/description change must |
| 14 | **Open decisions** | **PASS** — `TODO(SCHEMA_EVOLUTION_POLICY)`, the item gating this milestone, was resolved by ADR-0008 in this same change and is followed rather than reinterpreted. `TODO(GOLDEN_DATASET_LICENSING)` gates Milestone 6 and `TODO(PRE_1_0_VERSIONING)` gates first release; neither is touched. One decision is **surfaced rather than resolved silently** — see item 2 below |

### Design decisions that refine the spec

Recorded so reviewers see them here rather than discovering them in code. None is a constitution
violation.

1. **A provider is named, and the constitution names a different one as an example.** The plan chooses
   Anthropic Claude (research.md R1). The constitution's packaging line reads "Provider integrations are
   optional extras (for example `docdoc[openai]`, `docdoc[pdf]`)" — an illustration of *extras*, in a
   sentence about packaging, while the sanctioned-stack line says only "one LLM adapter". The choice is
   made on three concrete properties (generally available server-enforced structured output, a 1M-token
   context window, documented prefix-match prompt caching), and it is a one-module change to revisit —
   which is the point of Principle IV. If the project reads the constitution's example as binding, the
   fix is a one-line constitution amendment naming the intended provider, **not** a quiet substitution
   during implementation.

2. **ADR-0003's `Extract` row lists decoding parameters that do not exist on the chosen provider.** It
   names "temperature, top_p, seed, max_tokens"; the current models of the chosen provider reject
   `temperature`, `top_p`, and `top_k` outright and have no seed. The plan follows ADR-0003's *rule* —
   fold every input that can change the result — and folds what actually exists: `model_id`,
   `model_version`, `max_tokens`, the effort level, and the thinking mode (research.md R4). Whether the
   ADR's parenthetical should be generalised is a decision for an ADR, not for this plan, so it is
   surfaced here rather than settled. Two consequences worth naming: the honest no-determinism position
   of FR-037 is now the *only* available position, and the effort level is a result-affecting input that
   the reference design never contemplated.

3. **The input-budget guard is a deliberate over-estimate.** FR-030 must refuse an over-budget document
   *before* transmission (FR-041), and the only exact token count available is an API call that
   transmits the document to answer. The guard is therefore a local, conservative character-based bound,
   with the provider's own rejection mapped to the same typed error as a backstop (research.md R5). The
   consequence to accept: a document that would have fitted can be refused. The ratio is configurable
   and the limitation is documented rather than discovered.

4. **`schema_hash` covers more than the wire does.** The hash is taken over the whole schema including
   constraints and descriptions; the response shape sent to the model is a projection that drops what the
   provider cannot enforce (research.md R3). So editing a numeric bound invalidates the extraction cache
   while changing nothing the model sees. That is correct under ADR-0008 — the constraint changes what a
   result *means* to Milestone 5 — but it looks like a spurious cache miss and is documented as such.

5. **The prompt assembly order is load-bearing, not stylistic.** The provider's cache is a prefix match,
   so the per-schema prefix must precede the document text and nothing per-request may appear ahead of
   the breakpoint (research.md R15). A test asserts it, because the failure mode is silent: the results
   stay correct and the bill multiplies.

6. **Two names are imported from `docdoc.ingest`.** `ProviderError` and `TransportSettings`
   (research.md R9). Legal under Principle X's order and recorded in the layers contract. The
   alternative — promoting both to the kernel — was rejected because a transport knob has no place in an
   IR whose purity is Principle I, and because there is no second consumer yet to justify a shared home.

## Project Structure

### Documentation (this feature)

```text
specs/003-schema-driven-extraction/
├── plan.md              # This file
├── spec.md              # Feature specification (50 FR, 21 SC, 5 clarifications)
├── research.md          # Phase 0 output — 15 resolved decisions
├── data-model.md        # Phase 1 output — entities, EXT-1…EXT-24, error model
├── quickstart.md        # Phase 1 output — setup and 5 validation scenarios
├── contracts/
│   └── extraction-api.md  # Phase 1 output — public API contract
├── checklists/
│   └── requirements.md    # Spec quality checklist (16/16)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

Single-project Python library. Only the paths below are created or touched by this feature; sibling
packages (`transform/`, `pipeline/`, `api/`) arrive in later milestones.

```text
pyproject.toml               # + [anthropic] extra, + import-linter layers/forbidden contracts
uv.lock                      # regenerated and committed

src/docdoc/
├── kernel/                  # UNCHANGED at this milestone
├── ingest/                  # UNCHANGED — two names imported from it (R9)
└── extraction/              # NEW LAYER
    ├── __init__.py          # the public surface in contracts/extraction-api.md
    ├── errors.py            # ExtractionError, SchemaError; re-exports ProviderError (R9)
    ├── schema.py            # Schema, FieldSpec, cardinality, the one-level bound (FR-048)
    ├── loader.py            # JSON → Schema, rejected at load time (FR-050)
    ├── identity.py          # schema_hash, prompt_hash, extraction artifact id (ADR-0003/0008)
    ├── registry.py          # SchemaRegistry — concrete versions only, no `latest` (FR-014)
    ├── shape.py             # schema → response shape projection, `response-shape@1` (R3, R7)
    ├── conform.py           # compiled pydantic model per schema; field-addressable failures (R10)
    ├── prompt.py            # PromptTemplate, assembly order and cache breakpoint (R8, R15)
    ├── budget.py            # the local input/output budget guard (R5)
    ├── adapter.py           # ModelAdapter protocol, ModelUsage, DecodingOptions
    ├── observe.py           # the single `extraction.extract` structured event
    ├── extract.py           # extract() — the entry point that composes the above
    └── adapters/
        ├── __init__.py
        ├── echo.py          # deterministic in-repo adapter — a deliverable, not a fixture (R11)
        └── anthropic_messages.py  # the one real adapter   (extra: anthropic)

schemas/                     # NEW — schemas are data, not code (FR-049)
├── invoice@1.json
├── invoice@2.json           # a second major, live alongside @1 (SC-008)
├── receipt@1.json           # the "second document type, zero engine changes" case (SC-014)
└── prompts/
    ├── invoice@1.md
    ├── invoice@2.md
    └── receipt@1.md

tests/
├── unit/
│   ├── test_schema_loader.py          # EXT-1…EXT-5, one-level bound, load-time rejection
│   ├── test_schema_identity.py        # EXT-6…EXT-9, hash stability and sensitivity
│   ├── test_registry.py               # EXT-10…EXT-13, concrete versions, concurrent majors
│   ├── test_shape_projection.py       # EXT-14, the enforceable subset (R3)
│   ├── test_conform.py                # EXT-15…EXT-18, absence vs empty, undeclared discard
│   ├── test_prompt_assembly.py        # EXT-19, cache-prefix ordering (R15)
│   ├── test_budget_guard.py           # EXT-20, over-estimate direction, no transmission
│   ├── test_extract_echo.py           # end-to-end offline, US1
│   ├── test_artifact_identity.py      # EXT-21…EXT-23, what moves the id and what must not
│   ├── test_grounding_untouched.py    # EXT-24 — every grounding field unresolved (SC-018)
│   ├── test_provider_errors.py        # transient vs permanent, refusal-as-200, deadline
│   ├── test_extraction_boundaries.py  # layer direction + no SDK leak
│   ├── test_no_provider_names.py      # SC-013's other half: no provider named outside adapters
│   ├── test_observe.py                # event schema + content-leak assertion (SC-015)
│   ├── test_no_transmission.py        # SC-016, against a recording transport
│   └── test_anthropic_mapping.py      # recorded, scrubbed responses (R11)
├── contract/
│   └── test_model_adapter_contract.py # every ModelAdapter must satisfy EXT-15…EXT-18
├── property/
│   └── test_schema_hash.py            # reorder ⇒ same hash; any semantic edit ⇒ different hash
├── integration/
│   └── test_anthropic_live.py         # marked `provider`
├── perf/
│   └── test_extraction_perf.py        # marked `perf`, SC-021
└── fixtures/
    ├── schemas/                       # incl. an over-nested schema and a malformed file
    ├── echo/                          # canned responses for the in-repo adapter (FR-044)
    ├── anthropic/                     # 3 recorded, scrubbed responses: ok, refusal, truncated
    └── snapshots/schema_hashes.json   # the FR-017 change detector
```

**Structure Decision**: `src/` layout unchanged. `extraction/` is one flat package with one module per
concept plus an `adapters/` sub-package — the only place a provider SDK may be imported, which is what
makes the containment rule expressible as a directory boundary rather than a convention. This mirrors
`ingest/parsers/` deliberately: a reader who has understood the ingest layer already knows where to look.
The in-repo `echo` adapter is a sibling of the real one under the same contract, so neither is privileged
in code and the offline path is not a special case.

`schemas/` sits at the repository root rather than inside the package because it is data a *deployment*
supplies, not data docdoc ships. The committed files are fixtures and examples; the registry loads from
paths that configuration names (FR-049).

Because `schemas/` is therefore **not in the wheel**, anything that must run for a `pip install docdoc`
user supplies its own schema rather than reading this directory. That applies to the SC-020 example above
all: it writes a minimal schema to a temporary directory and registers that, which also happens to be the
clearest demonstration available that a schema is data.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No gate fails. Three items are recorded not as violations but because a reviewer would reasonably ask why
they exist.

| Item | Why needed | Simpler alternative rejected because |
|---|---|---|
| A deterministic in-repo adapter shipped as part of the library, not as a test fixture | FR-044 and SC-019 require the whole path except the model call to run with no credentials. An adapter that lives in `tests/` cannot be exercised by the contract suite that every adapter must satisfy, and cannot be used in the documented example | Making it a test double. Rejected: the contract test in `tests/contract/` exists precisely to prove that *any* adapter satisfies EXT-15…EXT-18, and it needs at least two implementations to be meaningful. A double in `tests/` would also leave the documented quickstart requiring credentials, which SC-001 forbids |
| Recorded-response fixtures for the real adapter | Without them the response-to-result mapping — which produces every real extraction — is exercised only where credentials exist, so CI would never test it | Live integration tests alone. Rejected for the same reason Milestone 2 rejected it: an adapter tested only in credentialed environments regresses silently everywhere else |
| A checked-in `schema_hashes.json` snapshot | FR-017 requires that a registered version's hash cannot move without an explicit acknowledgement, and ADR-0008 is explicit that no system can detect a *semantic* change on its own | Trusting review to catch an unbumped breaking change. Rejected: that is precisely the failure ADR-0008 was written to prevent, and the snapshot is what converts a silent contract break into a failed build plus a commit-message classification |
