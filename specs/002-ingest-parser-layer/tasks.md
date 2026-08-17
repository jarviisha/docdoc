---

description: "Task list for the ingest parser layer"
---

# Tasks: Ingest Parser Layer

**Input**: Design documents from `/specs/002-ingest-parser-layer/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/ingest-api.md](contracts/ingest-api.md),
[quickstart.md](quickstart.md)

**Tests**: NOT optional here. The constitution's test mandate applies to three areas this feature
touches — **layer boundaries** (an automated dependency-direction test), **provider adapters**
(integration tests isolated so the unit suite runs without credentials), and the **kernel** (the
Milestone 1 property suite must stay green across the provenance change). Test tasks below are
therefore requirements, not suggestions.

**Golden-set metrics, deliberately absent**: the constitution's task template mandates a golden-set
metrics task for any change touching parsers, and this feature adds two. It is omitted because no
golden dataset exists yet — `TODO(GOLDEN_DATASET_LICENSING)` is open and gates Milestone 6 — and the
constitution makes the evaluation gate advisory until that dataset reaches its target size. This is
recorded rather than left silent, so the omission reads as a decision and not an oversight.

**Organization**: Grouped by user story so each is independently implementable and testable.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4, mapping to the user stories in spec.md
- Every task names its exact file path

## Path Conventions

Single Python project: `src/docdoc/`, `tests/`, `examples/` at repository root, per
[plan.md § Project Structure](plan.md).

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Make the new layer and its two optional extras installable and enforceable.

- [X] T001 Create the ingest package skeleton — `src/docdoc/ingest/__init__.py` and `src/docdoc/ingest/parsers/__init__.py` (empty public surfaces, filled by later tasks)
- [X] T002 Add the `pdf` and `azure` optional extras to `pyproject.toml` (`pymupdf`, `azure-ai-documentintelligence`), leaving base dependencies as `pydantic` alone (R1, R2, SC-010)
- [X] T003 Add the `provider` pytest marker to `[tool.pytest.ini_options]` in `pyproject.toml`, so live service tests are deselectable (FR-034)
- [X] T004 Extend the `import-linter` contracts in `pyproject.toml`: add `docdoc.ingest` above `docdoc.kernel` in the layers contract, and add a forbidden contract barring `pymupdf`/`fitz`/`azure`/`httpx` from every ingest module except `docdoc.ingest.parsers.*` (R13, FR-026)
- [X] T005 Regenerate and commit `uv.lock` with the new extras (Principle VIII — reproducibility)
- [X] T006 Write `tests/fixtures/make_fixtures.py` and commit the eight generated fixtures — `pdf/digital_invoice.pdf`, `pdf/scanned_contract.pdf`, `pdf/sparse_text_layer.pdf`, `pdf/mixed_pages.pdf`, `pdf/two_column.pdf`, `pdf/rotated_90.pdf`, `pdf/encrypted.pdf`, and `image/sample_page.png` — all synthetic, no real document content. The image is required by SC-004's sample set and by ING-13

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The shared plumbing every story needs — kernel provenance, the error model, the source
type, and the validation/normalization the adapters are held to.

**⚠️ CRITICAL**: No user story work begins until this phase is complete.

### Kernel change (additive)

- [X] T007 Extend `src/docdoc/kernel/provenance.py`: add `PageTextVerdict` and `TextLayerRecord`, and add the optional `text_layer` and `reading_order` fields to `IngestProvenance`, both defaulting to `None` (data-model §7)
- [X] T008 [P] Export `PageTextVerdict` and `TextLayerRecord` from `src/docdoc/kernel/__init__.py`
- [X] T009 [P] Test the provenance extension in `tests/unit/test_provenance_fields.py` — ING-18, ING-19, plus Milestone 1 back-compat: a provenance built without the new fields is still valid, and `document_id` is unchanged by their presence
- [X] T010 Run `tests/property` unchanged and confirm the Milestone 1 kernel suite is still green (constitution gate 13)

### Ingest primitives

- [X] T011 [P] Implement the error hierarchy in `src/docdoc/ingest/errors.py` — `IngestError`, `UnsupportedDocumentError`, `ParserCapabilityError`, `ParserError`, `ProviderError`, each with its `reason` vocabulary (data-model §8, R10)
- [X] T012 [P] Implement `ParserCapabilities` and `CapabilityRequest` in `src/docdoc/ingest/capabilities.py` (data-model §2)
- [X] T013 [P] Implement `ParseOptions` helpers and `TransportSettings` as two separate types in `src/docdoc/ingest/options.py` — transport settings must be structurally incapable of reaching `options_hash` (ING-5, ING-6, FR-039)
- [X] T014 [P] Implement `SourceFile`, byte-signature detection, and `Limits` in `src/docdoc/ingest/source.py` (ING-1, ING-2, ING-3, R9)
- [X] T015 [P] Test byte-signature detection in `tests/unit/test_source_detection.py` — declared type ignored, PNG-named-`.pdf` resolved by content, unknown signature rejected
- [X] T016 [P] Test limit enforcement in `tests/unit/test_limits.py` — over-size and over-page rejected before any parse or transmission
- [X] T017 [P] Define the `Parser` protocol in `src/docdoc/ingest/parser.py` — `id`, `version`, `capabilities`, `reading_order`, `parse()` (contracts §5)
- [X] T018 [P] Implement coordinate normalization, rotation resolution, and token/text assembly in `src/docdoc/ingest/normalize.py` (R6, R7, R8)
- [X] T019 [P] Test normalization in `tests/unit/test_normalize.py` — the 1% out-of-page tolerance is clamped, anything beyond raises `GeometryError`, assembled text spans align exactly with their tokens, and **no normalization is applied**: runs of whitespace survive, hyphenated line breaks are not rejoined, and no table is linearized into the text (FR-007)
- [X] T020 [P] Property-test normalization in `tests/property/test_geometry_normalization.py` — every produced box lies within `0..1` for arbitrary page sizes and rotations
- [X] T021 [P] Implement output validation in `src/docdoc/ingest/validate.py` — token order, capability honesty, geometry bounds; raises `ParserError`, never repairs (ING-4, ING-8, FR-037)
- [X] T022 [P] Test validation in `tests/unit/test_validate.py` — out-of-order and overlapping tokens rejected with the parser named; a parser declaring geometry that omits it rejected
- [X] T023 [P] Implement the single structured event in `src/docdoc/ingest/observe.py` — `ingest.parse` under the `docdoc.ingest` logger (contracts §8, FR-040)
- [X] T024 [P] Test the event in `tests/unit/test_observe.py` — every documented field present on success and failure, and zero document text or credentials in output (FR-029, SC-013)
- [X] T025 Add the boundary test in `tests/unit/test_ingest_boundaries.py` — no upward imports, no provider SDK outside `parsers/`, and `lint-imports` passes (constitution mandate, SC-008)
- [X] T026 Export the foundational surface from `src/docdoc/ingest/__init__.py` per [contracts/ingest-api.md](contracts/ingest-api.md)

**Checkpoint**: The layer exists, is enforced, and is testable — adapters can now be written.

---

## Phase 3: User Story 1 — Parse a text-bearing PDF offline (Priority: P1) 🎯 MVP

**Goal**: A digital PDF becomes a `Document` whose text ranges resolve to pages and boxes, with no
credentials and no network.

**Independent Test**: Run `examples/parse_pdf.py` against `digital_invoice.pdf` with the network
disabled; `find()` then `locate()` returns a box over the invoice number.

### Tests for User Story 1

> Write these first and confirm they fail before implementing T029.

- [X] T027 [P] [US1] Write the shared parser contract suite in `tests/contract/test_parser_contract.py` — parameterized over any `Parser`, asserting ING-4, ING-7, ING-8, ING-9
- [X] T028 [P] [US1] Write `tests/unit/test_pdf_text_parser.py` covering the digital, blank-page, and mixed fixtures

### Implementation for User Story 1

- [X] T029 [US1] Implement the PyMuPDF adapter in `src/docdoc/ingest/parsers/pdf_text.py` — word-level tokens, pages with dimensions and rotation, normalized geometry, `id="pdf-text"`, `reading_order="pymupdf-layout@1"`, and `version` embedding the library version (R1, R5, R6)
- [X] T030 [US1] **Resolve the R5 assumption**: add a `two_column.pdf` case to `tests/unit/test_pdf_text_parser.py` asserting column one precedes column two. If the library's sorted extraction does not deliver that, change the declared `reading_order` identifier to describe actual behaviour rather than intended behaviour
- [X] T031 [US1] **Resolve the R8 assumption**: add a `rotated_90.pdf` case asserting geometry lands over the glyphs as displayed. If coordinates arrive unrotated, apply the page rotation matrix in `normalize.py` and record it
- [X] T032 [US1] Implement `parse()` in `src/docdoc/ingest/parse.py` — detect type, enforce limits, run the native adapter, validate, return a `Document`; routing and registry arrive in US2/US4. The module is named `parse`, not `pipeline`, so it does not collide with the Pipeline layer of Milestone 7
- [X] T033 [US1] Test the end-to-end native path in `tests/unit/test_parse_native.py` — identical `document_id`, text, and geometry across two runs, **and the converse**: changing parser id, parser version, or options yields a different `document_id`, while changing only `TransportSettings` yields the same one (SC-005, SC-006, SC-018, ING-5); a blank page present with zero tokens; the whole test runs offline
- [X] T034 [P] [US1] Write `examples/parse_pdf.py` — a runnable example needing only `docdoc[pdf]` (SC-015)

**Checkpoint**: The default path works end to end. This is the MVP.

---

## Phase 4: User Story 2 — The text-layer decision, explicit and recorded (Priority: P2)

**Goal**: Routing is decided before parsing, by a versioned rule, and the verdict — per page and for
the document — is readable off any result without touching the source file.

**Independent Test**: Assess the digital, scanned, sparse, and image fixtures with no parser invoked;
each yields the expected verdict plus the evidence behind it.

### Tests for User Story 2

- [X] T035 [P] [US2] Write `tests/unit/test_assess_text_layer.py` — digital → usable, scanned → not usable, `sparse_text_layer.pdf` → not usable (the case that proves the threshold does real work), image → not usable without inspection, repeated assessment byte-identical, and with the native reader absent: an unforced parse raises `ParserCapabilityError` while a forced one skips the assessment with `rule_not_run` recorded (ING-10 … ING-13)

### Implementation for User Story 2

- [X] T036 [US2] Implement `TextLayerRule` (`text-layer@1`), `PageTextVerdict`, and `TextLayerAssessment` in `src/docdoc/ingest/assess.py` — per-page character counts excluding whitespace, control, and replacement characters; document verdict by majority; `rule_not_run` set when the native reader is absent (R3, R4, ING-10, ING-11)
- [X] T037 [US2] **Validate the R3 thresholds** (100 chars/page, 50% of pages) against the committed fixture set. If the set disagrees, adjust the defaults and bump the rule id to `text-layer@2` — the numbers are a starting point, not a measurement
- [X] T038 [US2] Wire routing and the `force` override into `src/docdoc/ingest/parse.py` — assessment runs before selection; an override is honoured and the overridden verdict preserved; `force` skips the assessment entirely when it cannot run, so a recognition-only install can still parse PDFs (FR-012, FR-013)
- [X] T039 [US2] Populate `provenance.text_layer` and `provenance.reading_order` on every document both paths produce (FR-011, FR-035, ING-19)
- [X] T040 [P] [US2] Test recording in `tests/unit/test_provenance_recording.py` — verdict present for 100% of pages, readable without re-reading the source, override recorded, and the mixed fixture's scanned page identifiable as expected-empty (SC-003)

**Checkpoint**: Routing is explicit and inspectable; US1 and US2 both work.

---

## Phase 5: User Story 3 — Scanned documents and images through the service (Priority: P3)

**Goal**: A scanned PDF or image produces a document of exactly the same shape as the native path,
with bounded, loud failure behaviour.

**Independent Test**: With credentials, one scanned page returns a document satisfying every kernel
invariant, its geometry normalized and no service type anywhere in it. Without credentials, the
recorded-response tests still pin the mapping.

### Tests for User Story 3

- [X] T041 [P] [US3] Record and scrub service responses into `tests/fixtures/azure/` — generated from synthetic documents, containing no real content and no account identifiers (R14)
- [X] T042 [P] [US3] Write `tests/unit/test_azure_mapping.py` against those recordings — response → IR mapping, coordinate conversion, tables, and the absence of any service field name in the result
- [X] T043 [P] [US3] Write `tests/unit/test_provider_errors.py` — transient failures retried to the configured limit then raised; `auth` raised on the first attempt with zero retries; deadline exceeded raises naming the bound; no credentials raises before any byte is read; no fallback to another parser ever (ING-21, FR-014, SC-017)
- [X] T044 [P] [US3] Write `tests/integration/test_azure_live.py`, marked `provider` — skipped with a stated reason when credentials are absent (FR-034, SC-009)

### Implementation for User Story 3

- [X] T045 [US3] Implement the Azure Document Intelligence adapter in `src/docdoc/ingest/parsers/azure_di.py` — SDK imported here and nowhere else, `id="azure-di"`, `reading_order="azure-di-service@1"`, version embedding the service API version, provider exceptions translated with `__cause__` preserved (R2, ING-20)
- [X] T046 [US3] Implement the retry, backoff, and deadline policy in the adapter using `TransportSettings`, configuring the SDK's own retry policy to docdoc's values so attempts are not multiplied (R12, FR-038)
- [X] T047 [US3] Route the recognition path through `src/docdoc/ingest/parse.py` so a not-usable verdict reaches this adapter

**Checkpoint**: Both paths produce indistinguishable documents; all three stories work.

---

## Phase 6: User Story 4 — Ask for a capability, never for a provider (Priority: P4)

**Goal**: Selection is capability-driven, deterministic, and inspectable, and no application code
names a provider.

**Independent Test**: Two stub parsers with differing capabilities; requesting one only the second
satisfies selects the second, and registering them in the opposite order changes nothing.

### Tests for User Story 4

- [X] T048 [P] [US4] Write `tests/unit/test_registry_selection.py` — ING-14 (deterministic, order-independent), ING-15 (no-match error names the capability and lists candidates), ING-16 (unavailable parser present with a reason, not dropped), ING-17 (no fallback after failure)

### Implementation for User Story 4

- [X] T049 [US4] Implement `ParserRegistry`, priority ordering, availability tracking, and `default_registry()` in `src/docdoc/ingest/registry.py` — default priority `("pdf-text", "azure-di")`, offline before service-backed, `parser_id` as final tie-break (R11, FR-016)
- [X] T050 [US4] Replace the direct adapter wiring in `src/docdoc/ingest/parse.py` with registry selection driven by `CapabilityRequest`
- [X] T051 [P] [US4] Add `tests/unit/test_no_provider_names.py` — assert docdoc's own modules and `examples/` select by capability and never by provider id (SC-011)

**Checkpoint**: All four stories independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T052 [P] Add `tests/perf/test_ingest_perf.py` (marked `perf`) and **revise the plan.md performance table with the measured figures** — the current numbers are targets, not measurements
- [X] T053 [P] Add `tests/unit/test_temp_file_cleanup.py` — no temporary file survives a successful parse or an induced failure (FR-030, SC-014)
- [X] T054 [P] Update `README.md`: roadmap Milestone 2 → **Done**, Milestone 3 → Next, and add a parsing example to "What it does today"
- [X] T055 [P] Document the PyMuPDF AGPL-3.0 implication in `README.md` and in the extras documentation — docdoc stays Apache-2.0, the extra is opt-in, and a closed-source embedder needs to know before installing (plan.md design decision 1)
- [X] T056 [P] Write `docs/concepts/ingest.md` — the two paths, the text-layer decision, and capability-based selection
- [X] T057 [P] Update `CHANGELOG.md` for the ingest layer and the additive kernel provenance change
- [X] T058 Run every scenario in [quickstart.md](quickstart.md) V1–V5 end to end, including the two `--no-project` base-install checks
- [X] T059 Run the full gate: `pytest`, `mypy --strict src/docdoc`, `ruff check`, `ruff format --check`, `lint-imports`. Extend `.github/workflows/ci.yml` to a `{ubuntu, macos, windows}` OS matrix — SC-005 claims byte-identical output across platforms, and the current single-OS matrix (Python versions only) cannot verify that claim
- [X] T060 Reconcile `spec.md`, `plan.md`, and `data-model.md` with what was actually built — following the Milestone 1 precedent, record any place the design turned out not to be implementable rather than leaving the artifacts describing an intent the code does not match

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (T001–T006)**: no dependencies; T006 needs T005 (the `pdf` extra installed)
- **Foundational (T007–T026)**: depends on Setup — **blocks all user stories**
- **US1 (T027–T034)**: depends on Foundational
- **US2 (T035–T040)**: T035–T037 depend only on Foundational and can run alongside US1; T038–T039 touch `parse.py` and therefore follow T032
- **US3 (T041–T047)**: depends on Foundational; T042–T044 are written first, in the TDD sense, but can only pass once T045 exists — T044 in particular needs both the adapter and credentials; T047 follows T038
- **US4 (T048–T051)**: depends on Foundational; T050 follows T032 and is cleanest after T045 exists to register
- **Polish (T052–T060)**: after the stories you intend to ship

### Story Independence — an honest note

The four stories are independently *testable*, but three of them write to the same composition root,
`src/docdoc/ingest/parse.py`: US1 creates it (T032), US2 adds routing (T038), US3 adds the second
path (T047), US4 replaces direct wiring with the registry (T050). Those four tasks are strictly
sequential and must not be parallelized, even though their stories otherwise can be.

Everything else — the assessment, the two adapters, the registry — is independently developable and
has its own tests that do not go through `parse.py`.

### Within Each Story

- Tests are written first and must fail before the implementation task that satisfies them
- Primitives before adapters; adapters before composition
- T030, T031, and T037 are **assumption-resolution tasks**: each may change a declared identifier or a
  default. Treat a surprise there as information, not as a failure to be worked around

### Parallel Opportunities

- T008 + T009 after T007
- T011–T024 are almost entirely parallel — twelve independent modules and their tests
- T027 + T028 together; T035 alongside all of US1
- T041 + T042 + T043 + T044 together, before T045
- T052–T057 all parallel

---

## Parallel Example: Foundational Phase

```bash
# After T007–T010 (the kernel change), launch the ingest primitives together:
Task: "Implement the error hierarchy in src/docdoc/ingest/errors.py"
Task: "Implement ParserCapabilities and CapabilityRequest in src/docdoc/ingest/capabilities.py"
Task: "Implement ParseOptions and TransportSettings in src/docdoc/ingest/options.py"
Task: "Implement SourceFile, signature detection, and Limits in src/docdoc/ingest/source.py"
Task: "Define the Parser protocol in src/docdoc/ingest/parser.py"
Task: "Implement normalization in src/docdoc/ingest/normalize.py"
Task: "Implement output validation in src/docdoc/ingest/validate.py"
Task: "Implement the ingest.parse event in src/docdoc/ingest/observe.py"
```

---

## Implementation Strategy

### MVP First (User Story 1 only)

1. Phase 1 Setup → Phase 2 Foundational → Phase 3 US1
2. **Stop and validate**: quickstart V1 with the network disabled
3. At this point docdoc can turn a real digital PDF into a locatable `Document` — the first time the
   Milestone 1 kernel is reachable from a file rather than from a test

### Incremental Delivery

1. Setup + Foundational → the layer exists and is enforced
2. **+ US1** → the offline default path (MVP, quickstart V1)
3. **+ US2** → routing is explicit and recorded (quickstart V2)
4. **+ US3** → scanned and image documents (quickstart V4)
5. **+ US4** → provider-neutral selection (quickstart V3)
6. Polish → V5, perf figures, docs, roadmap

US4 last is deliberate and matches the spec's priority: the registry is the boundary that keeps later
milestones from ever naming a provider, so it must exist before extraction work begins — but it is not
what makes the first parse work.

### Parallel Team Strategy

After Foundational, three tracks run cleanly side by side: the native adapter (US1), the assessment
(US2 T035–T037), and the remote adapter with its recorded fixtures (US3 T041–T045). The four
`parse.py` tasks are the one place the tracks must queue.

---

## Notes

- `[P]` means a different file with no incomplete dependency
- Verify each test fails before writing the code that satisfies it
- Commit after each task or logical group
- T030, T031, T037, T052, and T060 exist because this plan makes claims it has not verified. Each is a
  place where reality gets to correct the design, and the artifact gets updated rather than the
  finding being discarded

---

## Phase 8: Convergence

Appended by `/speckit-converge` after the implementation pass. Each task traces to the artifact that
called for it and to the kind of gap found. No constitution MUST is violated, so nothing here is
CRITICAL.

- [X] T061 Enforce the page limit on the forced path in `src/docdoc/ingest/parse.py` per FR-028 (partial) — when the text-layer rule is skipped, `verdict.pages` is empty and `check_page_count` never runs, so an over-limit document can be transmitted to a paid service; either obtain the page count another way or refuse explicitly because the limit cannot be checked
- [X] T062 Give `UnsupportedDocumentError` a `parser_id` in `src/docdoc/ingest/errors.py` and pass it from `parsers/pdf_text.py` per FR-025 and SC-007 (partial) — an encrypted or corrupt PDF is refused by a *named* parser, but the error omits it and the `ingest.parse` event records `parser_id=None`
- [X] T063 Derive page indices from one source in `src/docdoc/ingest/parsers/azure_di.py` per FR-005 and SC-002 (partial) — tokens are numbered by `enumerate` while tables use `pageNumber - 1`; the two agree only when `pageNumber == index + 1`, and a page-ranged response currently fails with a misleading "geometry outside page N" rather than naming the real cause
- [X] T064 Check all four capabilities in `check_capability_honesty` in `src/docdoc/ingest/validate.py` per FR-004 and ING-4 (partial) — `text` and `handwriting` are never verified, and the `if capabilities.tables and document.tables: return` branch is dead
- [X] T065 Add a recorded service response for `tests/fixtures/image/sample_page.png` and an offline test in `tests/unit/test_azure_mapping.py` per US3/AC3 (missing) — "an image yields a single-page document" is currently verified only by the live test, which is skipped without credentials
- [X] T066 Add a Unicode fixture and test per spec Edge Cases (missing) — ligatures, combining marks, characters outside the BMP, and right-to-left script have no coverage, and assembling canonical text from tokens is exactly where offsets could drift; the fixture needs an embedded font carrying the glyphs
- [X] T067 Test the `Retry-After` translation in `_from_http` in `tests/unit/test_provider_errors.py` per FR-038 (partial) — honouring a service-supplied wait is tested, but the code that reads the header into `retry_after_s` is not
- [X] T068 Reconcile `validate.py` with plan.md per plan: validation responsibilities (partial) — the plan says validation checks geometry bounds; the code checks token order and capability honesty only, with bounds enforced at `BBox` construction. Either add the check or correct the plan
- [X] T069 Remove the unused `width`, `height`, and `page_index` parameters from `_bbox_of` in `src/docdoc/ingest/parsers/azure_di.py` (unrequested) — the function uses none of them
- [X] T070 Cover a zero-page PDF per spec Edge Cases (missing) — PyMuPDF refuses to write one (`cannot save with zero pages`), so the fixture must be hand-crafted; if that proves impractical, record in the spec's edge-case list that the case is unreachable through the supported toolchain rather than leaving it looking covered

---

## Phase 9: Convergence (second pass)

Appended by a second `/speckit-converge`. No constitution MUST is violated, so nothing here is
CRITICAL. Three of the seven are artifacts that drifted *because of* Phase 8: fixing the code moved
the documents out of date, which is exactly the drift a second pass exists to catch.

- [X] T071 Stop dropping table cells silently in `src/docdoc/ingest/parsers/azure_di.py` per FR-022 and US3/AC4 (partial) — a cell without a span is skipped while the table keeps declaring its full dimensions, so a 2x2 table can carry three cells with nothing recording that the fourth vanished; either refuse the table outright or record how many cells were unplaceable
- [X] T072 Declare `retry_after_s` as a real field on `ProviderError` in `src/docdoc/ingest/errors.py` and add it to data-model §8 per data-model: error model (partial) — it is currently attached dynamically with a `type: ignore[attr-defined]` and read back with `getattr`, so the documented error model does not mention the one attribute the retry loop depends on
- [X] T073 Correct ING-2 in `specs/002-ingest-parser-layer/data-model.md` per data-model: ING-2 (partial) — it still claims limits are checked "before any parse or transmission", which T061 made untrue for the forced path, where the page count is not known until after the parse
- [X] T074 Correct the limits paragraph in `specs/002-ingest-parser-layer/contracts/ingest-api.md` §2 per contracts: Limits (partial) — it describes the page limit as checked during assessment only, omitting the post-parse check T061 added
- [X] T075 Assert the provider swap end to end in `tests/unit/test_registry_selection.py` per US4/AC5 (missing) — parse the same bytes through two registries differing only in which parser is available, and confirm that text, tokens, and geometry are unchanged while provenance and identity differ; priority ordering is tested, but the claim the acceptance scenario actually makes is not
- [X] T076 Update the fixture counts in the `specs/002-ingest-parser-layer/plan.md` source tree per plan: project structure (partial) — it says 7 synthetic PDFs; T066 and T070 brought it to 9
- [X] T077 Extend the coverage table and scenarios in `specs/002-ingest-parser-layer/quickstart.md` per quickstart: validation guide (partial) — SC-002, SC-004, and SC-015 are absent from "What done looks like", and no scenario mentions the Unicode fixture, the zero-page PDF, or the image recorded response

---

## Phase 10: Convergence (third pass)

Appended by a third `/speckit-converge`. No constitution MUST is violated, so nothing here is
CRITICAL.

The first item is a task from Phase 9 that was marked complete having been only half done. Worth
naming plainly: T072 required both a code change and an artifact change, the code change was made,
and the checkbox went to `[X]`. A pass that only re-reads the code would not have caught it.

- [X] T078 Add `retry_after_s` to the `ProviderError` row of data-model §8 per T072 and data-model: error model (partial) — the field was declared on the class in Phase 9 but never documented; the retry loop's one input still appears in no artifact
- [X] T079 Fix the self-contradiction in `specs/002-ingest-parser-layer/plan.md` per plan: validation responsibilities (contradicts) — the Summary says validation checks "token order, capability honesty, and geometry bounds" while the reconciliation section in the same file says it does not check bounds and should not; T068 corrected the source tree and missed the prose
- [X] T080 Correct ING-22 in `specs/002-ingest-parser-layer/data-model.md` per data-model: ING-22 (partial) — it claims *every* error names the responsible parser, but T062 deliberately leaves `parser_id` unset when the refusal precedes any parser choice, and a test asserts exactly that; the invariant should say "the responsible parser, where one was chosen"
- [X] T081 Update the fixture count in plan.md Scale/Scope per plan: scale and scope (partial) — it says 6–8 small documents; the set is now 9 PDFs, 1 image, and 2 recorded service responses
- [X] T082 Either assert or drop the "nothing was transmitted" claim in `specs/002-ingest-parser-layer/quickstart.md` V5 per quickstart: validation guide (missing) — the over-limit rejection is tested but the claim that nothing reached a parser is not; a recording stub parser would settle it, and an unverified claim in a validation guide is worse than no claim

---

## Phase 11: Convergence (fourth pass)

Appended by a fourth `/speckit-converge`. No constitution MUST is violated, so nothing here is
CRITICAL — but the first item is the first *behavioural* defect found since Phase 8, and it was
sitting under a test that asserted it was correct.

- [X] T083 Stop jittering a service-supplied wait interval in `_sleep_before_retry` in `src/docdoc/ingest/parsers/azure_di.py` per FR-038 (contradicts) — jitter is applied after `retry_after_s`, so a service asking for 30 s is retried after 17-22 s; measured across six runs, five fired earlier than asked. FR-038 requires honouring the interval, and retrying early against a service that has just rate-limited you is how the next 429 is earned. Treat a service-supplied interval as a floor: jitter belongs on docdoc's own backoff, and may only extend a requested wait, never shorten it
- [X] T084 Correct `test_the_retry_loop_reads_it_without_getattr` in `tests/unit/test_convergence_gaps.py` per T072 (contradicts) — it asserts `waits[0] <= 0.25 * 1.5`, which encodes the F1 defect as the expected result and would fail once the code is right; it should assert the wait is never shorter than what the service asked for
- [X] T085 Document the service-supplied wait in `docs/concepts/ingest.md` per FR-038 (partial) — the Failure section describes attempts, backoff, jitter, timeout, and deadline, but omits the one part of the policy the service itself controls

---

## Phase 12: Convergence (fifth pass)

Appended by a fifth `/speckit-converge`. Both items are behavioural, and both were found by running
the code with hostile input rather than reading it — the same method that found the Phase 11 defect,
applied to areas never measured before.

- [X] T086 **CRITICAL** — Translate every service-payload access in `src/docdoc/ingest/parsers/azure_di.py` into `ParserError` per Constitution IV and §Error model, FR-025, ING-20, SC-007 (contradicts) — a malformed response leaks raw `KeyError` and pydantic `ValidationError` through the public API. Measured across six hostile responses, five leaked: a word missing `span`, a word missing `polygon`, a span missing `offset`, a page with `width: null`, and a cell missing `rowIndex`. The constitution requires errors to be stable, typed, and provider-neutral, and `KeyError('span')` is none of the three. Apply the same defence to `pdf_text.py`, whose input shape is more stable but not guaranteed
- [X] T087 Emit the `ingest.parse` event for *every* failure in `src/docdoc/ingest/parse.py` per FR-040 (partial) — the handler catches only `IngestError`, so an exception from anywhere else escapes with no event at all; measured zero events when a parser raised `KeyError`. Catch broadly, log, and re-raise, so a failure outside the error model still leaves a trace. Once T086 lands this becomes rare, but "rare" is not the same as "recorded"

---

## Phase 13: Convergence (sixth pass)

Appended by a sixth `/speckit-converge`. No constitution MUST is violated. Both items were found by
exercising the public API with inputs it accepts but was never tried with, continuing the method that
found the Phase 11 and 12 defects.

Three areas were probed and found sound, recorded here so a later pass need not repeat them:
malformed PDFs through the native path (six cases, zero untyped leaks), `Limits` and
`TransportSettings` at boundary values, and a suspicion that PyMuPDF writes to stderr behind docdoc's
back — measured per case and false.

- [X] T088 Apply `limits` to a caller-supplied `SourceFile` in `src/docdoc/ingest/parse.py` per FR-028 and contracts §1 (partial) — `parse(source_file, limits=...)` silently ignores the size and media-type limits, because they are only enforced inside `SourceFile.from_bytes`; measured a 2915-byte file parsing under `max_size_bytes=64`. `parse()` documents the parameter as "Enforced before any parse or transmission", and `SourceFile` is a first-class input form, so either re-check on entry or refuse a `SourceFile` whose construction limits are unknown
- [X] T089 Stop silently rewriting the requested media type in `_select` in `src/docdoc/ingest/parse.py` per FR-022 and Constitution VIII (contradicts) — a caller asking for `media_type="image/png"` on PDF bytes gets a PDF parse and no indication the request was changed. Either raise when the request contradicts the detected type, or state in contracts §4 that `media_type` in a `CapabilityRequest` is advisory and the bytes always decide; the line that rewrites it currently carries no explanation either way

---

## Phase 14: Convergence (seventh pass)

Appended by a seventh `/speckit-converge`. No constitution MUST is violated by the shipped adapters,
so nothing here is CRITICAL — but T090 closes the gap that would make a *third-party* adapter able to
break the identity guarantee unnoticed, which is the one the `Parser` protocol openly invites.

- [X] T090 Check that a parser's output corresponds to its input, in `src/docdoc/ingest/validate.py` per FR-002, ADR-0002, and Constitution I (missing) — nothing compares the returned `Document` with the file it was asked about. Measured with a stub parser returning another file's document: accepted, with a mismatched `blob_id`, provenance naming `pdf-text` while the selected parser was `impostor`, the wrong page count, and the routing verdict ignored; the log event then reported the selected parser's id beside a foreign `document_id`. Add three checks — `document.source.blob_id` equals the input's, `provenance.parser_id`/`parser_version` equal the selected parser's, and `provenance.text_layer` is the verdict that was passed in — raising `ParserError(reason="capability_mismatch")` or a new reason as fits. `validate_output` already receives `blob_id` and `parser_id` and uses them only to phrase errors
- [X] T091 Document the media-type rule in `docs/concepts/ingest.md` per T089 (partial) — contracts §4 records that a `CapabilityRequest` naming a type the bytes contradict is refused, but the concepts guide still shows the example without it, so the one place a reader looks first says nothing about a behaviour that now raises
