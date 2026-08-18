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
constraints, because Principle VII gives that to Milestone 5 — a choice, not a provider limit: the
chosen provider would enforce numeric bounds if asked, and they are deliberately not sent (research.md
R3).

What the layer does insist on is that the model's answer be *provably* the requested shape before it
becomes a result: the shape is enforced by the provider, checked again locally against a compiled model,
and a mismatch names the field rather than being coerced into place.

There is **no kernel change** at this milestone.

## Technical Context

**Language/Version**: Python >= 3.11 (unchanged from Milestones 1 and 2)

**Primary Dependencies**: Base install unchanged — `pydantic` only. One new optional extra:
`docdoc[google]` → the unified `google-genai` SDK ([research.md R1](research.md)). Stdlib used by the
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

| Operation | Target | Measured | Basis |
|---|---|---|---|
| Schema load + compile, per schema | < 200 ms | ~0.9 ms (3 schemas + prompts) | Once per schema at registration, not per extraction |
| Resolve + build request + conformance check + identity, 20-page doc / `invoice@1` | < 100 ms | **~0.19 ms** | SC-021 |
| Per-call cost inside a batch of 50 | — | ~0.18 ms | Equal to a single call, so no per-schema work leaked into `extract()` |
| `schema_hash` over a 100-field schema | < 10 ms | ~0.8 ms | Canonical JSON + one `sha256` |
| Projection, 25 → 200 fields | linear | 0.22 → 1.86 ms | 8× the fields for 8.5× the time |
| Conformance, 50 → 500 repeating-group entries | linear | 0.44 → 4.73 ms | 10× the entries for 10.7× the time |
| Input-budget guard, 166k characters | negligible | ~0.0004 ms | A character count (research.md R5) |
| Model call | recorded, not targeted | — | Dominated by the provider; governed by `deadline_s` |

Measured on a contributor laptop with `uv run pytest -m perf`, best-of-5. The deterministic path has
roughly **500× headroom** against SC-021, which is the honest reading: the criterion was never going
to be tight, because everything expensive in an extraction is on the other side of the network.

The targets sit far above the measurements on purpose, for the reason Milestone 2 recorded: a perf test
that trips on machine noise gets disabled, and a disabled test protects nothing. What these catch is an
accidental per-extraction schema recompile — the batch row is the one that would move — or a quadratic
in the projection or the conformance walk, not constant-factor drift.

**The 500× headroom above is extraction's, and it is not the suite's.** Stating it without this
qualifier — as an earlier revision of this section did — reads as though every perf test is comfortable.
One is not, and it is the number a reader needs.

**This milestone edited Milestone 1's perf tests, which is worth stating plainly.** Adding a blocking
`perf:` job to CI was the first time `-m perf` had ever run under an enforcing gate, and it immediately
found three kernel tests red or coin-flipping: document construction was sampling *once* at 247–369 ms
against a 300 ms budget, so whether the suite passed depended on what else the machine was doing. The
fix changed their measurement method to best-of-N and **did not touch the budgets** — moving a budget to
match a measurement makes the test agree with whatever the code currently does, which is the failure mode
a perf test exists to prevent. That the edit crosses a milestone boundary is the reason it is recorded
here rather than left in a commit message: a Milestone 1 test now reads differently because of Milestone
3 work, and nothing else says so.

**A live risk ships with this milestone.** The kernel's whole-document slice measures **279 ms best-of-5
against its 300 ms budget — 1.07× headroom**, against extraction's ~500×. CI runners are typically slower
than the laptop that produced that number, so the new `perf:` job may well be red on its first real run.
That is a known and accepted state, not an oversight: the correct response is to re-measure on the runner
and decide whether the budget was always wrong or the slice genuinely regressed. The response that is
*not* correct is `continue-on-error`, which would turn the gate this milestone just added back into
decoration. `.github/workflows/ci.yml` carries the same warning at the point someone would edit it.

**Constraints**: The whole layer except the model call must run with no credentials and no network
(FR-044). Provider SDK types confined to `docdoc/extraction/adapters/` and enforced by `import-linter`
(FR-023). Transport settings must not influence artifact identity (FR-027) — true by construction, since
they live in a separate type (research.md R9). No clock, randomness, or I/O may be introduced into
`docdoc/kernel/`; the extraction layer may use all three. Document text, extracted values, claimed source
text, prompt content, and credentials never reach logs (FR-039).

**Scale/Scope**: Documents up to the configured input budget; schemas up to a few hundred fields.
Roughly 13 new modules plus one adapter and the in-repo `echo` adapter, no kernel change, and a fixture
set of 3 schemas, 3 prompts, and 4 recorded provider responses.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against constitution v1.2.0. **Initial: PASS. Post-design re-check: PASS.**

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** | **PASS** — no kernel change at all at this milestone (research.md R9 rejects promoting `ProviderError` for want of a present-tense need). `Document` is read and never modified (FR-009). `tests/unit/test_kernel_purity.py` must keep passing untouched |
| 2 | **Provenance preservation (I, VIII)** | **PASS** — every result records document identity, schema identity **and** `schema_hash`, `prompt_hash`, model id and version, decoding settings, adapter id and version, extractor version, token usage, and the derived artifact id (data-model §8). Re-extraction produces a new result; nothing is overwritten (FR-038) |
| 3 | **Grounding integrity (II)** | **PASS by deliberate abstention** — every value carries the ADR-0004 grounding fields and this feature leaves all of them unresolved (FR-032, FR-047). What it *does* supply is the byte-faithful claimed source text (FR-003) that Milestone 4 will resolve. Nothing is self-certified as grounded, and `model_confidence` is stored untrusted and routes nothing |
| 4 | **Determinism (III)** | **PASS** — the deterministic core (schema resolution, request assembly, conformance check, identity) has no clock, randomness, or network. The model is the probabilistic edge and is declared as such: FR-037 refuses to claim byte-identical repeats. `temperature=0.0` and a recorded `seed` reduce variance, and the provider guarantees no bit-exactness, so the claim stays unavailable — research.md R4 |
| 5 | **Provider isolation (IV)** | **PASS** — SDK imports confined to one adapter module and enforced by a new forbidden-imports contract; provider exceptions translated with `__cause__` preserved; base install pulls no provider SDK (SC-013). A refusal — a *successful* response carrying a stop reason, in four categories plus a prompt-level block — is translated at the same boundary (research.md R12) |
| 6 | **Text-first (V)** | **N/A** — no parsing and no recognition at this milestone. This feature consumes whatever `Document` ingest produced and cannot re-route it |
| 7 | **Schema-driven (VI)** | **PASS** — this gate is the feature. Schemas and prompts are data files (FR-049); a second document type is added in the test suite with zero engine changes (SC-014), asserted by a check that fails the build; every result names the exact `name@version` and its content hash |
| 8 | **Validation separation (VII)** | **PASS** — extraction checks response shape and type parseability only (FR-006). Field constraints are *stored* in the schema and hashed into its identity, and deliberately not acted on; cross-field rules do not exist here. research.md R3 records that this split is required by the principle and **not** forced by the provider, which would enforce numeric bounds if asked |
| 9 | **No silent fallback (VIII)** | **PASS** — an unknown schema identity names the requested identity and the available versions (FR-016); a missing credential names the unavailable capability before any byte is transmitted (FR-041); a failed model call never switches model, provider, or schema version (FR-029); an over-budget document is refused rather than truncated (FR-030) |
| 10 | **Measurability (IX)** | **PASS** — all 21 success criteria are countable. Extraction *accuracy* is Milestone 6's golden-set question and is not claimed here; what is measured here is conformance, identity, provenance completeness, and boundary containment |
| 11 | **Layer direction (X)** | **PASS** — `docdoc.extraction` sits above `docdoc.ingest`, which is the constitution's own order, and is added to the `import-linter` layers contract in the same change. Only two names are imported from ingest and both are justified in research.md R9 |
| 12 | **MVP discipline (XI)** | **PASS** — nothing from the Deferred Technology list. No cache, no queue, no vector store, no second adapter, no schema-authoring tool. Repetition is bounded to one level rather than left open (FR-048), and every new type traces to a spec requirement in data-model.md |
| 13 | **Kernel test rigor (XII)** | **PASS** — no span, geometry, or kernel operation semantics change, so the Milestone 1 property suite applies unchanged and must stay green. New property tests cover the canonical-form/hash relationship: field reordering must not move `schema_hash`, and any field/type/constraint/description change must |
| 14 | **Open decisions** | **PASS** — `TODO(SCHEMA_EVOLUTION_POLICY)`, the item gating this milestone, was resolved by ADR-0008 in this same change and is followed rather than reinterpreted. `TODO(GOLDEN_DATASET_LICENSING)` gates Milestone 6 and `TODO(PRE_1_0_VERSIONING)` gates first release; neither is touched. One decision is **surfaced rather than resolved silently** — see item 2 below |

### Design decisions that refine the spec

Recorded so reviewers see them here rather than discovering them in code. None is a constitution
violation.

1. **A provider is named, and the choice is close to arbitrary.** The plan chooses Google Gemini
   (`docdoc[google]`, the unified `google-genai` SDK) because the project owner chose it. The
   constitution's packaging line names `docdoc[openai]` as an illustration of *extras*, in a sentence
   about packaging, while the sanctioned-stack line says only "one LLM adapter" — so it does not bind.
   Every major provider offers server-enforced structured output, a large enough context window, and
   prefix caching, and adding a second adapter is a new module of roughly 150 lines. research.md R1
   records this honestly, including that an earlier revision chose Anthropic, justified it with three
   properties that are not differentiators, and was written by an Anthropic model that did not disclose
   the conflict.

2. **A multi-provider aggregator was considered and rejected.** LiteLLM would let one adapter serve many
   models, which is a real benefit. It is rejected because the enforcement level of the response shape
   would depend on the model string with docdoc unable to tell which level it got — turning FR-011's
   "the response could not have had another shape" back into "our parser coped" — and because it
   normalises decoding parameters, so what provenance records as sent may not be what the provider
   received. Both defeat the point of this layer. The `ModelAdapter` protocol already makes a second
   provider a new module rather than an engine change (research.md R1).

3. **The prompt prefix is currently too short to be cached at all.** The ordering is right — stable
   before volatile — but a cache hit needs the shared prefix to clear a per-model minimum of 2,048–4,096
   tokens, and the current per-schema prefix is a few hundred (research.md R15). So the ordering buys
   nothing today. It is recorded rather than dressed up, and padding the prefix to become cache-eligible
   is explicitly *not* chosen without measurement.

4. **The input-budget guard is a deliberate over-estimate.** FR-030 must refuse an over-budget document
   *before* transmission (FR-041), and the only exact token count available is an API call that
   transmits the document to answer. The guard is therefore a local, conservative character-based bound,
   with the provider's own rejection mapped to the same typed error as a backstop (research.md R5). The
   consequence to accept: a document that would have fitted can be refused. The ratio is configurable
   and the limitation is documented rather than discovered.

5. **`schema_hash` covers more than the wire does.** The hash is taken over the whole schema including
   constraints and descriptions; the response shape sent to the model is a projection that drops what the
   provider cannot enforce (research.md R3). So editing a numeric bound invalidates the extraction cache
   while changing nothing the model sees. That is correct under ADR-0008 — the constraint changes what a
   result *means* to Milestone 5 — but it looks like a spurious cache miss and is documented as such.

6. **The prompt assembly order is load-bearing, not stylistic.** The provider's cache is a prefix match,
   so the per-schema prefix must precede the document text and nothing per-request may appear ahead of
   the breakpoint (research.md R15). A test asserts it, because the failure mode is silent: the results
   stay correct and the bill multiplies.

7. **Two names are imported from `docdoc.ingest`.** `ProviderError` and `TransportSettings`
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
pyproject.toml               # + [google] extra, + import-linter layers/forbidden contracts
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
    ├── adapter.py           # ModelAdapter protocol, ModelUsage, ExtractionOptions
    ├── adapter_registry.py  # AdapterRegistry, default_adapter() — FR-021's mechanism.
    │                        #   Application code calls default_adapter(); it never
    │                        #   constructs a provider. The echo adapter is excluded
    │                        #   from automatic selection structurally (research.md R16)
    ├── value.py             # ExtractedValue, ValueTree — the grounding fields, left unresolved
    ├── retry.py             # the retry loop, once for every adapter (R9, FR-026). Transport
    │                        #   policy belongs to the layer, not to each adapter: otherwise
    │                        #   "at most N attempts bounded by a deadline" becomes a claim
    │                        #   about whichever adapter you happened to use
    ├── observe.py           # the single `extraction.extract` structured event
    ├── extract.py           # extract() — the entry point that composes the above
    └── adapters/
        ├── __init__.py
        ├── echo.py          # deterministic in-repo adapter — a deliverable, not a fixture (R11)
        └── gemini.py        # the one real adapter          (extra: google)

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
│   ├── test_adapter_registry.py       # FR-021: selection by configuration, and the
│   │                                  #   structural refusal to auto-select the echo adapter
│   ├── test_extraction_has_no_document_type_code.py  # SC-014's automated check —
│   │                                  #   forbidden names derived from schemas/, not hardcoded
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
│   ├── test_gemini_mapping.py         # recorded, scrubbed responses (R11)
│   ├── test_schema_versioning.py      # US2 end to end: concurrent majors, no `latest`,
│   │                                  #   a description edit moving the hash and not the version
│   ├── test_schema_snapshot.py        # FR-017's change detector, and its own remedy text
│   ├── test_provenance_recording.py   # SC-011, every recorded field non-empty and real
│   ├── test_no_fallback.py            # FR-029, against a registry with somewhere to fall back to
│   ├── test_reextraction.py           # FR-038/FR-043, results independent and frozen
│   ├── test_plan_tree_is_current.py   # this tree — asserts it did not go stale a fourth time
│   ├── test_provider_tests_are_separable.py  # FR-045 — a credential-reading test must be marked
│   └── test_documented_api_references_resolve.py  # SC-020 — the docs' API is the real API
├── contract/
│   └── test_model_adapter_contract.py # every ModelAdapter must satisfy EXT-15…EXT-18,
│                                      #   run against all three including the real one
├── property/
│   └── test_schema_hash.py            # reorder ⇒ same hash; any semantic edit ⇒ different hash
├── integration/
│   ├── test_gemini_live.py            # marked `provider`
│   └── test_examples_run.py           # SC-020 — the examples are executed, not read
├── perf/
│   └── test_extraction_perf.py        # marked `perf`, SC-021
└── fixtures/
    ├── schemas/                       # incl. an over-nested schema and a malformed file
    ├── echo/                          # canned responses for the in-repo adapter (FR-044)
    ├── gemini/                        # 4 recorded, scrubbed responses: ok, refusal, recitation, truncated
    └── snapshots/schema_hashes.json   # the FR-017 change detector
```

**On enumerating test files here.** This list went stale three times: an analysis pass named three
missing files, a convergence phase added the two *that finding named*, and the next pass found six more.
The obvious reading is that the enumeration is the wrong shape and the tree should describe directories
instead — but that would lose what makes it useful. Every line maps a requirement (an `EXT-`, `SC-`, or
`FR-`) to the test that holds it, and no other artifact provides that map.

So it is kept and checked instead. `tests/unit/test_plan_tree_is_current.py` asserts that every test
file importing `docdoc.extraction` appears here, which is mechanically derivable and precise. That is
the same shape as the adapter-coverage and example-coverage assertions, both added for the same reason:
a hand-maintained list goes stale exactly when someone is not looking at it.

**The rule has a known blind spot, recorded rather than glossed.** Testing the new check against the
suite showed files escaping it: `test_examples_run.py` runs the examples as subprocesses,
`test_plan_tree_is_current.py` reads the plan, and the two added in Phase 15 read test sources and docs
respectively. None imports the package an import-based scan looks for.

These were first handled as a two-item exemption capped by an assertion reading "if test files routinely
exercise this feature without importing it, the rule needs rethinking rather than extending". Phase 15
added two more, so the assertion fired as designed and the rethink followed: they are a **category** —
*meta-tests*, which check a property of the repository rather than the behaviour of the package, and
therefore cannot import it — rather than a list of exceptions. The category carries its own invariant:
no entry may import `docdoc.extraction`, so it cannot be used to park a real package test out of the
derivable rule's view. A check that quietly covers less than it appears to is the exact defect this
milestone's review passes kept finding, so its bound is named instead of assumed.

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
