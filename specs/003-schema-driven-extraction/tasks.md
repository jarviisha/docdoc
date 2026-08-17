# Tasks: Schema-Driven Extraction

**Input**: Design documents in `/specs/003-schema-driven-extraction/`
**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/extraction-api.md](contracts/extraction-api.md)

**Tests are not optional here.** The tasks template's docdoc override makes them mandatory for **layer
boundaries** (this feature creates a layer) and **provider adapters** (this feature adds one), per
Principles III, VIII, and XII. Beyond that, 21 of the spec's success criteria are countable assertions,
so most of them are a test rather than a claim.

**A note on where the model fits.** Every task except the ones marked `provider` runs with no credentials
and no network, against the in-repo `echo` adapter. That is FR-044, and it is why the echo adapter is a
Phase 2 deliverable rather than a Phase 5 afterthought.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4, mapping to the user stories in spec.md
- Every task names its exact file path

## Path Conventions

Single Python project: `src/docdoc/`, `schemas/`, `tests/`, `examples/` at repository root, per
[plan.md § Project Structure](plan.md).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Make the new layer and its one optional extra installable and enforceable.

- [X] T001 Create the extraction package skeleton — `src/docdoc/extraction/__init__.py` and `src/docdoc/extraction/adapters/__init__.py` (empty public surfaces, filled by later tasks)
- [X] T002 Add the `google` optional extra to `pyproject.toml`, leaving base dependencies as `pydantic` alone (R1, SC-013)
- [X] T003 Extend the `import-linter` layers contract in `pyproject.toml` to `["docdoc.extraction", "docdoc.ingest", "docdoc.kernel"]` (Principle X, FR-023)
- [X] T004 Add an `import-linter` forbidden contract in `pyproject.toml` barring `google`/`httpx`/`openai` from every `docdoc.extraction` module except `docdoc.extraction.adapters.*`, with `ignore_imports` naming `docdoc.extraction.adapters.gemini -> google` and `unmatched_ignore_imports_alerting = "none"` so an ignore for a module that does not exist yet is not a misconfiguration (R1, FR-023)
- [X] T005 Regenerate and commit `uv.lock` with the new extra (Principle VIII — reproducibility)
- [X] T006 [P] Write the committed schema fixtures — `schemas/invoice@1.json`, `schemas/invoice@2.json` (a second major differing by one added optional field, so ADR-0008's no-bump-needed case is also exercised), `schemas/receipt@1.json` (the second document type of SC-014) — and their prompts under `schemas/prompts/`. `invoice@1` must include a scalar, a nested group, a repeating group, an `enum`, a `date`, a `decimal`, and at least one numeric constraint that the wire projection will drop (R3)
- [X] T007 [P] Write the rejection fixtures under `tests/fixtures/schemas/` — `malformed.json` (not parseable), `unknown_type.json`, `duplicate_field.json`, `over_nested.json` (a repeating group inside a repeating group), and `no_prompt@1.json` — one per FR-050 / EXT-3 rejection path

**Checkpoint**: `uv sync --all-extras` succeeds, `lint-imports` runs, and the fixtures exist.

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Everything more than one story needs — the schema types, identity, the registry, the wire
projection, the conformance check, the prompt, the budget guard, and both ends of the adapter contract.

**⚠️ CRITICAL**: No user story work begins until this phase is complete.

### The layer boundary (constitution mandate, do this first)

- [X] T008 Write `tests/unit/test_extraction_boundaries.py` asserting the dependency direction and that no provider SDK name appears in any `docdoc.extraction` module outside `adapters/` — the automated boundary test the constitution requires for every new layer. It must fail informatively while the layer is still empty rather than pass vacuously
- [X] T009 [P] Implement `src/docdoc/extraction/errors.py` — `ExtractionError` and `SchemaError` rooted at the existing `DocdocError`, and a re-export of `ProviderError` from `docdoc.ingest` (R9, data-model §9). No new class named `ProviderError`

### Schema, loading, and the bound

- [X] T010 Implement `src/docdoc/extraction/schema.py` — `FieldType`, `Cardinality`, `FieldSpec`, and `Schema` (data-model §1, §2). Sampling settings live in `adapter.py`, not here
- [X] T011 Implement the structural checks in `src/docdoc/extraction/schema.py` — EXT-1 (sibling name uniqueness), EXT-2 (children iff group or repeating group), EXT-3 (**the one-level repetition bound**, error naming the limit and the offending field path), EXT-4 (constraint keys recognised, never applied) (FR-004, FR-048, FR-006)
- [X] T012 Implement `src/docdoc/extraction/loader.py` — JSON → `Schema`, every defect rejected at load time with the file and the defect named, no partial construction (FR-050, R6)
- [X] T013 [P] Write `tests/unit/test_schema_loader.py` covering EXT-1…EXT-4 and every fixture from T007, including that `over_nested.json` is refused at load rather than at first use

### Identity

- [X] T014 Implement `src/docdoc/extraction/identity.py` — `schema_hash` and `prompt_hash` as `sha256` over `docdoc.kernel.identity.canonical_json`, reusing the kernel's rule rather than a second convention (FR-013, R6, EXT-6)
- [X] T015 Implement `options_hash` and `extraction_artifact_id` in `src/docdoc/extraction/identity.py` per ADR-0003 and data-model §8. The folded set is `schema_identity`, `schema_hash`, `prompt_hash`, `projection_id`, `model_id`, `model_version`, `max_output_tokens`, `temperature`, `top_p`, `top_k`, `seed`, `thinking_budget`, `input_budget_tokens`. Every parameter ADR-0003's Extract row names exists on this provider, so the row is followed literally rather than refined (R4, FR-034)
- [X] T016 [P] Write `tests/unit/test_schema_identity.py` covering EXT-6…EXT-9. EXT-9 is the subtle one and must be pinned explicitly: a pure `@1` → `@2` bump with no other edit leaves `schema_hash` unchanged, and the two artifacts still differ because the identity is folded separately
- [X] T017 [P] Write `tests/property/test_schema_hash.py` (Hypothesis) — over randomly generated schemas, reordering fields never moves `schema_hash`, and any change to a field, type, cardinality, `required` flag, constraint, or description always moves it (SC-005)

### Registry

- [X] T018 Implement `src/docdoc/extraction/registry.py` — `SchemaRegistry.from_paths`, `register`, `resolve`, `identities`, `describe`, and the `default_registry()` helper of [contracts/extraction-api.md](contracts/extraction-api.md) §2, which reads the paths configuration names and nothing more. `resolve` accepts a concrete `name@version` only; there is no `latest` and no partial match (FR-014, FR-018, EXT-10, EXT-13)
- [X] T019 [P] Write `tests/unit/test_registry.py` covering EXT-10…EXT-13, including that a schema failing any check leaves the registry byte-identical to what it was

### The wire projection and the conformance check

- [X] T020 Implement `src/docdoc/extraction/shape.py` — the `Schema` → `ResponseShape` projection, identified as `response-shape@1`. It carries types, cardinality, `enum`, and string formats, sets `additionalProperties: false` at every object, asks for each field as a `value`/`claimed_text` pair, and **drops** numeric bounds, array-length bounds, `minLength`/`maxLength`, and `pattern`. Only the last two are unenforceable by the provider; the rest are dropped **by choice**, because Principle VII puts constraint enforcement in Milestone 5 (R3, R7, EXT-14, FR-011)
- [X] T021 [P] Write `tests/unit/test_shape_projection.py` asserting the enforceable subset is carried and the unenforceable one is dropped — and that what is dropped is still present in the `Schema` and still inside `schema_hash`, because that is the property Milestone 5 depends on (R3)
- [X] T022 Implement `src/docdoc/extraction/conform.py` — compile each schema to a `pydantic` model once at registration, cache it under the schema identity, and validate responses against it. A failure raises `ExtractionError` naming the field path (R10, EXT-15, FR-007)
- [X] T023 Implement absence handling in `src/docdoc/extraction/conform.py` — every declared field present in the result, an omitted field recorded as an explicit absence distinguishable from a returned-empty value, and an undeclared field discarded with the occurrence recorded (EXT-16, EXT-17, FR-002, FR-005, FR-008)
- [X] T024 Implement byte-faithful `claimed_text` handling in `src/docdoc/extraction/conform.py` — no trimming, no case folding, no Unicode normalisation (EXT-18, FR-003)
- [X] T025 [P] Write `tests/unit/test_conform.py` covering EXT-15…EXT-18, with cases for a model that returns a scalar where a repeating group was asked for and vice versa, a value with no claimed text, claimed text with no value, and a field returned twice

### Prompt, budget, and the adapter contract

- [X] T026 Implement `src/docdoc/extraction/prompt.py` — `PromptTemplate`, and request assembly ordered stable-to-volatile with the cache breakpoint at the end of the per-schema prefix (R8, R15, EXT-19, FR-020)
- [X] T027 [P] Write `tests/unit/test_prompt_assembly.py` asserting EXT-19: the per-schema prefix is byte-identical across two different documents, and nothing per-request — no timestamp, no document id, no request id — appears before the breakpoint. This test exists because the failure is silent: results stay correct and the bill multiplies (R15)
- [X] T028 Implement `src/docdoc/extraction/budget.py` — the local, network-free input-budget guard, deliberately over-estimating, raising `ExtractionError` naming the document, the bound, the estimate, and narrowing with `Document.slice` as the way forward. Include a documented default `input_budget_tokens`, provisional until T079 measures it (proposed starting value: 200,000 tokens — comfortably under the default model's 1M context window while leaving room for output and reasoning) (R5, EXT-20, FR-030, FR-046)
- [X] T029 Implement the character-to-token ratio in `src/docdoc/extraction/budget.py` with a deliberately pessimistic starting value and a named safety margin, both in one module-level constant with a comment saying they are provisional until T079 measures them. The guard must be wrong in the refusing direction, never in the transmitting one (R5)
- [X] T030 [P] Write `tests/unit/test_budget_guard.py` asserting EXT-20 and, specifically, that the guard over-estimates rather than under-estimates on every committed fixture, and that it runs before any transport call
- [X] T031 Implement `src/docdoc/extraction/adapter.py` — the `ModelAdapter` protocol, `ModelUsage`, `ExtractionOptions`, and `Availability`. There is one options type, not two: `ExtractionOptions` is what a caller passes and what provenance records. Transport settings are **not** on it: `TransportSettings` comes from `docdoc.ingest`, which is what makes FR-027 true by construction (R9, data-model §6, §7)
- [X] T032 Implement `src/docdoc/extraction/adapters/echo.py` — the deterministic in-repo adapter, with `from_fixtures(path)` reading responses keyed by `(document_id, schema identity)` plus the `malformed()` and `refusing()` constructors the failure tests need; and commit the response fixtures under `tests/fixtures/echo/` covering `invoice@1`, `invoice@2`, and `receipt@1`. This is a library deliverable, not a test double (R11, FR-044)
- [X] T033 [P] Write `tests/contract/test_model_adapter_contract.py` — the contract every adapter must satisfy (EXT-15…EXT-18, exactly one response or a typed error, never partial), parameterised over every registered adapter so it is meaningful rather than tautological
- [X] T034 Implement `src/docdoc/extraction/observe.py` — the single `extraction.extract` structured event carrying identifiers, model and adapter identity and version, usage, duration, attempts, and outcome, and nothing else (FR-040)

**Checkpoint**: The layer exists, is boundary-enforced, and is testable end to end offline — the adapters
can now be written and `extract()` can be composed.

---

## Phase 3: User Story 1 — Ask a document for the fields a schema declares (Priority: P1) 🎯 MVP

**Goal**: A developer hands over a document and a schema identity and receives one entry per declared
field, each with a typed value and the verbatim source text the model claims it came from.

**Independent test**: Register a schema, extract a committed document through the echo adapter with no
credentials and no network, and confirm every declared field appears with its value and claimed text.

### Tests for User Story 1

- [X] T035 [P] [US1] Write `tests/unit/test_extract_echo.py` — the end-to-end offline path: one entry per declared field, a repeating group returned as its own set of fields per occurrence, an absent field explicitly marked absent, and claimed text preserved byte-for-byte (SC-001, SC-002, SC-003)
- [X] T036 [P] [US1] Extend `tests/unit/test_extract_echo.py` with the refusal-to-repair cases: a response that is not the requested shape, one that omits a declared field, and one carrying an undeclared field — each producing the outcome FR-007/FR-008 require rather than a coerced result
- [X] T037 [P] [US1] Write `tests/unit/test_no_provider_names.py` asserting SC-014's two halves: zero document-type-specific code paths anywhere under `src/docdoc/extraction/`, and no provider or model name outside `adapters/`. The check must fail if a future change adds `if schema.name == "invoice"` anywhere

### Implementation for User Story 1

- [X] T038 [US1] Implement `extract()` in `src/docdoc/extraction/extract.py` — resolve the schema, guard the budget, build the request, call the adapter, check conformance, assemble the result. Exactly one result or an explicit error; never a partial result (FR-001)
- [X] T039 [US1] Assert non-mutation in `src/docdoc/extraction/extract.py` and pin it in `tests/unit/test_extract_echo.py`: the `Document`, its canonical text, and its provenance are unchanged by an extraction — on the success path **and** on every failure path (conformance failure, budget refusal, adapter error). The failure half is Principle XII's "provider failure never corrupts the canonical document", which that principle lists as MUST-be-tested (FR-009)
- [X] T040 [US1] Add the `receipt@1` case to `tests/unit/test_extract_echo.py` — a second document type extracted with zero engine changes, which is Principle VI read literally (SC-014)
- [X] T041 [US1] Fill the public surface in `src/docdoc/extraction/__init__.py` with the names in [contracts/extraction-api.md](contracts/extraction-api.md) §1–§3 and §8

**Checkpoint**: The feature works end to end with no credentials. This is the MVP.

---

## Phase 4: User Story 2 — Depend on a schema version that means something (Priority: P2)

**Goal**: A developer pins to `invoice@1`, their stored results say `invoice@1` forever, and the number
moves only when the contract actually breaks.

**Independent test**: Register two majors of one schema, extract against each, confirm each result names
the exact identity used; then edit a description without bumping and confirm the version holds while the
content hash moves.

### Tests for User Story 2

- [X] T042 [P] [US2] Write `tests/unit/test_schema_versioning.py` — two majors registered at once resolve independently and neither shadows the other; each result names the exact identity and hash; the two artifact ids differ (SC-004, SC-008, EXT-11)
- [X] T043 [P] [US2] Extend `tests/unit/test_schema_versioning.py` with the resolution failures: a bare `invoice` with no version, an unregistered version, and an unregistered name — each naming the requested identity and what actually exists, with no neighbouring version substituted (SC-006, EXT-12)
- [X] T044 [P] [US2] Write `tests/unit/test_schema_snapshot.py` and commit `tests/fixtures/snapshots/schema_hashes.json` — the FR-017 change detector: the build fails when a registered version's hash moves. The test's own message must state the two ways to clear it (publish a new major, or refresh the snapshot with the classification in the commit message), because a failing check whose remedy is unclear gets bypassed (SC-007)

### Implementation for User Story 2

- [X] T045 [US2] Implement `describe()` and `identities()` fully in `src/docdoc/extraction/registry.py` — a caller lists every registered identity and reads fields, types, and descriptions without running an extraction (FR-018)
- [X] T046 [US2] Implement the concrete-version-only guard in `src/docdoc/extraction/extract.py` — an identity without a version, or any implicit resolution, raises `SchemaError` from the library core (FR-014)
- [X] T047 [US2] Document the bump rules in `docs/concepts/extraction.md` with ADR-0008's table, and state plainly the consequence R3 surfaces: editing a numeric constraint moves `schema_hash` and invalidates the extraction cache while changing nothing the model sees. That looks like a spurious cache miss and must be documented rather than discovered

**Checkpoint**: The schema contract is real and enforced; US1 and US2 both work offline.

---

## Phase 5: User Story 3 — Reach a real model without naming it in application code (Priority: P3)

**Goal**: Extraction runs against a real model; application code never names a provider, a model family,
or a version, and swapping any of them is configuration.

**Independent test**: With credentials, extract one committed document, then repoint configuration at a
different model and confirm the same application code runs with only provenance changing.

### Tests for User Story 3

- [X] T048 [P] [US3] Write `tests/unit/test_gemini_mapping.py` against the recorded, scrubbed responses committed under `tests/fixtures/gemini/` — a successful structured response, a content refusal, and a truncated response. These keep the mapping code that produces every real result under test in CI (R11)
- [X] T049 [P] [US3] Write `tests/unit/test_provider_errors.py` — transient failures (connection, timeout, rate limit, server error, overloaded) retried within the `TransportSettings` limit; rejected credential, malformed request, unknown model, request too large, and **content refusal** each failing on the first attempt with zero retries. Include the two bounds separately: a per-attempt timeout, and an overall deadline that expires mid-retry — including the case where the service asks for a wait longer than the remaining deadline, which must fail on the deadline rather than sleep past it (FR-025, FR-026, SC-017, R12)
- [X] T050 [P] [US3] Add the refusal-as-success case to `tests/unit/test_provider_errors.py` explicitly: the provider returns a *successful* HTTP response whose stop reason is a refusal, and the adapter must branch on the stop reason before reading content. An adapter that reads content unconditionally would report a refusal as an answer, which is the trap this test exists to catch (R12)
- [X] T051 [P] [US3] Write `tests/unit/test_no_transmission.py` against a transport that records every call attempt — zero bytes transmitted for a request that fails schema resolution, credential availability, or the budget guard (SC-016, FR-041)
- [X] T052 [P] [US3] Write `tests/integration/test_gemini_live.py`, marked `provider` — one live extraction, skipped with a stated reason when credentials are absent (FR-045, SC-019)

### Implementation for User Story 3

- [X] T053 [US3] Implement `src/docdoc/extraction/adapters/gemini.py` — request construction from the `ResponseShape`, provider-enforced structured output, and response mapping. The SDK import lives here and nowhere else (R1, R2, FR-023)
- [X] T054 [US3] Implement stop-reason branching in `src/docdoc/extraction/adapters/gemini.py` — check the stop reason before touching content; map a refusal to a permanent `ProviderError` carrying the provider's stated category, and a truncated response to the output-budget `ExtractionError` of FR-030 rather than to a retry (R12, R14)
- [X] T055 [US3] Implement error translation in `src/docdoc/extraction/adapters/gemini.py` — every provider exception becomes a docdoc error with `__cause__` preserved; no provider exception crosses the boundary (FR-042)
- [X] T056 [US3] Wire `TransportSettings` from `docdoc.ingest` through `src/docdoc/extraction/extract.py` — attempt limit defaulting to three, exponential backoff with jitter, honouring a service-requested wait, bounded by a per-attempt timeout and an overall deadline (FR-026, R9)
- [X] T057 [US3] Implement `available()` in `src/docdoc/extraction/adapters/gemini.py` — a missing SDK or a missing credential reports unavailable **with the reason**, and `extract()` raises before any byte is transmitted (FR-028, FR-041)
- [X] T058 [US3] Assert in `tests/unit/test_gemini_mapping.py` that every folded decoding parameter actually reaches the request — `temperature`, `top_p`, `top_k`, `seed`, `max_output_tokens`, `thinking_budget`. All exist on this provider (R4), so the failure mode is the opposite of the one first written: a parameter folded into identity but never sent would make the artifact id claim something the call did not do
- [X] T059 [US3] **Resolve the R13 assumption by measuring, not guessing**: choose the default model tier, `thinking_budget`, and `max_output_tokens` by running the committed fixture set at each setting and recording accuracy, token usage, and latency in `research.md` under R13. Confirm the default model id against the live API — `DEFAULT_MODEL` in `adapters/gemini.py` is currently unverified. Size `max_output_tokens` with headroom for reasoning, which is billed from the same allowance — a budget sized for the JSON alone truncates mid-answer (R13, R14)
- [X] T060 [US3] Measure the cache in `tests/integration/test_gemini_live.py` — two documents against one schema, reading `usage.total_cached_tokens`. **A zero is the expected result today**: a hit needs the shared prefix to clear 2,048–4,096 tokens and the current per-schema prefix is a few hundred (R15). So assert the threshold arithmetic and record the measured prefix size, rather than asserting a hit that cannot happen yet
- [X] T060a [US3] Add the four refusal branches to `tests/unit/test_gemini_mapping.py` and its fixtures — `SAFETY`, `PROHIBITED_CONTENT`, `BLOCKLIST`, and `RECITATION` — plus a prompt-level `promptFeedback.blockReason`. `RECITATION` needs its own assertion: it is not a safety refusal, an invoice quoting standard terms can trip it, and reporting it as one sends the caller after the wrong problem (R12)

**Checkpoint**: Real extractions work, and nothing downstream can tell which adapter produced a result
except by reading provenance.

---

## Phase 6: User Story 4 — Explain any extraction after the fact (Priority: P4)

**Goal**: Six months later, a stored result can be traced to the exact document, schema version, prompt,
model, and settings that produced it.

**Independent test**: Extract twice against the echo adapter with one input changed each time, and confirm
the artifact identity moves for every result-affecting input and holds for every input that cannot change
a result.

### Tests for User Story 4

- [X] T061 [P] [US4] Write `tests/unit/test_artifact_identity.py` covering EXT-21…EXT-23 — every folded input changes the id; retry, timeout, and deadline change it in zero cases; changing only the schema reuses the parse and triggers no re-parse (SC-009, SC-010)
- [X] T062 [P] [US4] Write `tests/unit/test_provenance_recording.py` — 100% of results record document identity, schema identity and hash, prompt hash, projection id, model identity and version, decoding settings, adapter identity and version, extractor version, and usage, all readable without re-running the extraction (SC-011)
- [X] T063 [P] [US4] Write `tests/unit/test_grounding_untouched.py` — every grounding field on every value of every result is unresolved, and no code path in the extraction layer sets one. This is SC-018 and it is the assertion that keeps Milestone 4's stage boundary from eroding (FR-032, FR-047, EXT-24)
- [X] T064 [P] [US4] Write `tests/unit/test_observe.py` — one structured event per extraction, success and failure alike, and zero occurrences of document text, extracted values, claimed source text, prompt content, or credentials anywhere in the log output while extracting the whole fixture set (SC-015, FR-039)

### Implementation for User Story 4

- [X] T065 [US4] Implement `ExtractionProvenance` and `ExtractionResult` in `src/docdoc/extraction/extract.py` per data-model §8, including `artifact_id`
- [X] T066 [US4] Implement `model_confidence` pass-through in `src/docdoc/extraction/extract.py` — stored verbatim, documented as untrusted in the field's own docstring and in `contracts/extraction-api.md` §3, and influencing no routing or acceptance decision (FR-031, ADR-0004)
- [X] T067 [US4] Implement the reserved calibration fields as always-`None` in `src/docdoc/extraction/extract.py`, per ADR-0004's MVP row
- [X] T068 [US4] Wire `observe.py` into `src/docdoc/extraction/extract.py` so the event is emitted on every path including the failure paths, and assert the failure paths in `tests/unit/test_observe.py` (FR-040)
- [X] T069 [US4] Implement `extractor_version` in `src/docdoc/extraction/extract.py`, embedding the adapter and SDK version the way `parser_version` embeds the library version, so an upgrade that changes output is visible in identity (FR-036)

**Checkpoint**: All four stories independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T070 [P] Write `tests/perf/test_extraction_perf.py`, marked `perf` — the deterministic budget of SC-021 (schema resolution, request construction, conformance check, identity under 100 ms for a 20-page document against a 20-field schema, model call excluded), plus a guard that a schema is compiled once at registration and not once per extraction
- [X] T071 [P] Revise the performance table in [plan.md](plan.md) against the measurements, as Milestones 1 and 2 both did. Unmeasured estimates written before implementation are not a baseline
- [X] T072 [P] Write `docs/concepts/extraction.md` — the layer, the two identities, the schema/wire projection split, the stage boundary with grounding, and the budget guard's honest limitation
- [X] T073 [P] Write `examples/extract_invoice.py` — the SC-020 example, runnable with no credentials against the echo adapter, with a commented line showing the switch to a real adapter. It must write its own minimal schema JSON to a temporary directory and register that, so it runs after `pip install docdoc` rather than only from a git checkout — `schemas/` is not packaged in the wheel. Writing the file is also the clearest possible demonstration that a schema is data
- [X] T074 [P] Update `README.md`: roadmap Milestone 3 → **Done**, Milestone 4 → Next; add an extraction example to "What it does today"; document the `docdoc[google]` extra alongside the existing ones
- [X] T075 [P] Add the `schemas/` directory conventions to `CONTRIBUTING.md` — how to add a document type, and what obliges a major bump per ADR-0008
- [X] T076 Run `/speckit-analyze` and append what it finds as a convergence phase in `specs/003-schema-driven-extraction/tasks.md`, the way Milestone 2 did. That pass found ten gaps the first pass left; assume this one leaves some too
- [X] T077 Confirm the gate in `.github/workflows/ci.yml` covers this layer — `pytest -m 'not provider and not perf'`, `mypy --strict`, `ruff`, `lint-imports` — and that it passes with no credentials configured, with every provider-marked skip stating its reason (SC-019)

---

## Dependencies & Execution Order

### Phase Dependencies

- **Phase 1 (Setup)** — no dependencies
- **Phase 2 (Foundational)** — depends on Phase 1. **Blocks everything else**
- **Phase 3 (US1)** — depends on Phase 2
- **Phase 4 (US2)** — depends on Phase 2. Independent of US1 in principle; see the note below
- **Phase 5 (US3)** — depends on Phase 2 and on US1's `extract()` composition (T038)
- **Phase 6 (US4)** — depends on Phase 2; its assertions are strongest once US1 and US3 both produce results
- **Phase 7 (Polish)** — depends on everything

### Story Independence — an honest note

The template asks for stories that are independently testable, and these are: each has an independent test
that passes without the others. But the phase boundaries are thinner than the spec's priority ordering
suggests, and pretending otherwise would mislead whoever schedules this.

- **US1 is the thinnest phase and the largest story.** Most of what US1 *needs* is in Phase 2, because
  US2, US3, and US4 need the same schema types, registry, projection, and conformance check. Phase 2 is
  therefore load-bearing for the MVP, not preliminary to it.
- **US2 is genuinely independent** and could be built first. It is P2 rather than P1 only because a
  version contract with nothing to version is hard to demonstrate.
- **US3 depends on US1** through `extract()`. There is no version of "reach a real model" that does not
  first have something to reach it with.
- **US4 is an assertion layer over the other three.** Its tests can be written early — T061 and T063
  especially — and will fail informatively until the provenance they assert exists.

### Within Each Story

Tests before implementation where the test defines the contract (T035/T036 before T038; T049/T050 before
T054; T061/T063 before T065). Types before the code that uses them. The boundary test (T008) comes before
any module it constrains, so it fails loudly on the first violation rather than being retrofitted.

### Parallel Opportunities

- **Phase 1**: T006 and T007 in parallel once T001–T005 land
- **Phase 2**: T009 in parallel with T010; then T013, T016, T017, T019, T021, T025, T027, T030, T033 are
  all separate test files with no shared state
- **Phase 3**: T035, T036, T037 in parallel
- **Phase 4**: T042, T043, T044 in parallel
- **Phase 5**: T048, T049, T050, T051, T052 in parallel; T053–T057 are one module and are **not** parallel
- **Phase 6**: T061, T062, T063, T064 in parallel
- **Phase 7**: everything except T076 and T077, which come last and in that order

---

## Parallel Example: Phase 2 test tasks

Once T010–T012, T014–T015, T018, T020, T022–T024, T026, T028, T031–T032 are in place, these nine test
files touch nothing in common:

```text
T013  tests/unit/test_schema_loader.py
T016  tests/unit/test_schema_identity.py
T017  tests/property/test_schema_hash.py
T019  tests/unit/test_registry.py
T021  tests/unit/test_shape_projection.py
T025  tests/unit/test_conform.py
T027  tests/unit/test_prompt_assembly.py
T030  tests/unit/test_budget_guard.py
T033  tests/contract/test_model_adapter_contract.py
```

---

## Implementation Strategy

### MVP First (Phase 1 → Phase 2 → User Story 1)

T001–T041. At that point a developer extracts structured values from a real document with no credentials,
no network, and no cost, against a schema that lives in a data file. That is the smallest thing worth
shipping and the smallest thing worth reviewing.

### Incremental Delivery

1. **Phases 1–3** — the MVP above. Reviewable on its own.
2. **Phase 4** — the version contract becomes enforced, and the snapshot check starts guarding it.
3. **Phase 5** — real extractions. The first phase that needs credentials and costs money.
4. **Phase 6** — the provenance and stage-boundary assertions close.
5. **Phase 7** — docs, example, measured performance, and the convergence pass.

### Parallel Team Strategy

Two people can work Phases 3–6 concurrently after Phase 2: one takes US1 then US3 (the request/response
path), the other takes US2 then US4 (identity, provenance, observability). The two touch `extract.py` from
opposite ends — composition versus provenance — so agree on `ExtractionResult`'s shape (data-model §8)
before either starts.

---

## Notes

- **Three tasks exist to resolve an assumption by measurement rather than to implement a decision**: T079
  (the budget guard's ratio and the default budget), T059 (the model tier, `thinking_budget`, and `max_output_tokens`), and T060
  (that the prompt cache actually reads). Milestone 2 had two such tasks and *both* assumptions turned out
  to be wrong, which is exactly why they were tasks. Do not close them by reasoning.
- **Until T079 lands, the budget guard runs on a guessed constant.** T028 and T029 ship a pessimistic
  starting ratio and a 200,000-token default, and both are provisional. That is the honest consequence of
  needing the provider to measure against: the MVP ships a number nobody has verified. It is safe in the
  refusing direction and unsafe in no direction, but it is not measured.
- **T037, T063, and T027 guard boundaries that erode silently.** A document-type conditional, a grounding
  status set one milestone early, and a volatile value in front of the cache breakpoint all leave the tests
  green and the design broken. They are cheap to keep and expensive to add back later.
- **No kernel change at this milestone.** `tests/unit/test_kernel_purity.py` and the Milestone 1 property
  suite must keep passing untouched. If a task appears to need a kernel change, that is a finding for
  review, not a licence.
- **The evaluation gate is advisory here and unreportable until Milestone 6.** Workflow gate 5 requires
  changes to prompts, models, or schemas to report golden-set metrics, and this feature introduces all
  three. There is no golden dataset yet — `TODO(GOLDEN_DATASET_LICENSING)` is open and gates Milestone 6 —
  so the gate is advisory during the MVP, as the constitution says. What *is* measured here is conformance,
  identity, provenance completeness, and boundary containment. Extraction accuracy is not claimed.

---

## Phase 8: Analysis remediation

Findings from `/speckit-analyze` on 2026-08-17. Four are coverage the first pass left; T079 is a task
that was in the wrong phase to be runnable at all, and was split out of T029 rather than left there.

- [X] T078 Extend `tests/unit/test_provider_errors.py` with Principle XII's canonical-document invariant against the real adapter's failure modes: after a content refusal, a per-attempt timeout, an exhausted retry chain, and a call interrupted mid-flight, the input `Document` is byte-identical to what was passed in and no partial result exists (Principle XII, FR-043)
- [X] T079 **Resolve the R5 assumption by measuring, not guessing**: calibrate the ratio, the safety margin, and the default `input_budget_tokens` in `src/docdoc/extraction/budget.py` against every committed fixture, using the provider's own token count as ground truth, and record the measured values in `research.md` under R5. If the ratio under-estimates for any fixture, raise the margin. Depends on T053 (R5, EXT-20, FR-030)
- [X] T080 [P] Write `tests/unit/test_no_fallback.py` — when the configured adapter fails permanently, fails transiently past its attempt limit, or is unavailable, the failure surfaces and the system tries no other adapter, no other model, and no other schema version. Assert against a registry holding two adapters and two schema majors, so a fallback would have somewhere to go if the code allowed one (FR-029, SC-012)
- [X] T081 [P] Write `tests/unit/test_reextraction.py` — extracting the same document twice, and extracting it under a newer schema major, each produces a new result with its own provenance and its own artifact id; the earlier result is unchanged in every field; and a failed extraction mutates no result that already exists (FR-038, FR-043)

**Checkpoint**: the three highest-severity findings of the analysis pass are closed, and the one task that
could not have run where it was written now sits after the adapter it depends on.

---

## Phase 9: Convergence

An assessment of the code against the spec, plan, and constitution — not against `tasks.md`, which
reported 82/82 done. Two of these are requirements that were tested against the *echo* adapter and
quietly fail against the real one; one is a record of work that a silent string replacement never
wrote.

- [ ] T082 Add configuration-driven adapter selection so application code never constructs a provider adapter, per FR-021 and US3/AC2 (missing). Today `README.md` and the quickstart both write `adapter=GeminiAdapter()`, which is literally "naming a provider in application code", and `contracts/extraction-api.md` §8 claims the opposite. The ingest layer's shape is the precedent: a registry plus a request that names what is needed rather than who supplies it. US3/AC2 — "when the configured model or provider changes, no application code changes" — cannot pass until this exists
- [ ] T083 Carry `document_id` on `ModelRequest` and thread it into every error the adapter raises, per SC-012 and FR-042 (partial). `src/docdoc/extraction/adapters/gemini.py` builds `ExtractionError` and `ModelProviderError` without it because the request does not carry it, so every adapter-raised failure reports `document_id=None`. SC-012 requires 100% of failures to name the document, the schema, and the adapter; only two of the three are named today
- [ ] T084 Name the bound and the actual size in the truncation error in `src/docdoc/extraction/adapters/gemini.py`, per FR-030 (partial). The message explains *why* truncation happens but reports neither the configured `max_output_tokens` nor the output actually produced, which `usage.candidates_token_count` carries. FR-030 requires "the document, the bound, and the actual size"
- [ ] T085 Restore the Phase 8 analysis-remediation record that commit `03106a0` describes and never wrote (missing). The seven fixes it lists were applied and verified; only the record is absent, because a `.replace()` matched nothing and was not asserted. Re-record them under new IDs rather than renumbering anything
- [ ] T086 [P] Decide and record whether `request id`, `processing id`, and `step id` belong in the `extraction.extract` event, per Constitution §Observability (partial). All three are pipeline concepts that arrive at Milestone 7, and the spec's FR-040 does not list them — but the constitution states them without that qualification, so the deferral should be explicit rather than implied by omission

**Checkpoint**: the two HIGH findings are the ones that matter. Both are requirements that pass against
the in-repo adapter and fail against the real one, which is the failure mode a fixture-only suite is
structurally unable to see.
