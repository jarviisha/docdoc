---

description: "Task list for Kernel and Canonical Document IR"
---

# Tasks: Kernel and Canonical Document IR

**Input**: Design documents from `/specs/001-kernel-document-ir/`

**Prerequisites**: [plan.md](plan.md), [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/kernel-api.md](contracts/kernel-api.md),
[quickstart.md](quickstart.md)

**Tests**: REQUIRED for this feature. Constitution Principle XII makes kernel property tests
non-optional, and spec SC-002/SC-005/SC-006/SC-009 state them as measurable outcomes. Within each
phase, write the tests first and confirm they fail before implementing.

**Organization**: Tasks are grouped by user story so each story can be implemented and verified
independently.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4, mapping to the user stories in spec.md
- Every task names its exact file path
- **T057–T058 were added after implementation**, when `/speckit-analyze` found requirements
  with no task covering them. Their IDs continue the sequence rather than renumbering the
  list, so existing references stay valid; they sit in the phase they logically belong to.

## Path Conventions

Single Python library, `src/` layout, per plan.md "Project Structure". Paths are repository-root
relative.

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Project skeleton and toolchain. Nothing here is docdoc-specific logic.

- [X] T001 Create `pyproject.toml` at repository root: project metadata (name `docdoc`, `requires-python = ">=3.11"`), runtime dependency `pydantic>=2.0`, dev dependency group (`pytest`, `pytest-cov`, `hypothesis`, `mypy`, `ruff`, `import-linter`), plus `[tool.ruff]`, `[tool.mypy]` (strict for `docdoc.kernel`), `[tool.pytest.ini_options]`, `[tool.coverage]`, and `[tool.importlinter]` contracts per research.md R10: a `layers` contract listing only `docdoc.kernel` (import-linter errors on layers naming modules that do not exist yet, so higher layers join as their milestones land) plus a `forbidden` contract barring provider SDKs from the kernel
- [X] T002 [P] Create package skeleton `src/docdoc/__init__.py` and `src/docdoc/kernel/__init__.py` (empty placeholders; the public surface is populated in T025)
- [X] T003 [P] Create test tree `tests/unit/`, `tests/property/`, `tests/fixtures/` with `tests/conftest.py` holding shared fixtures
- [X] T004 Generate and commit `uv.lock` via `uv sync --all-extras` — the lockfile is versioned per Principle VIII and is excluded from `.gitignore`
- [X] T005 [P] Create `.github/workflows/ci.yml` running, on Python 3.11 and 3.12: `pytest`, `mypy --strict src/docdoc/kernel`, `ruff check`, `ruff format --check`, and `lint-imports`

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Every entity and invariant that all four user stories require. No story can begin
until a `Document` can be constructed and an invalid one rejected.

**⚠️ CRITICAL**: No user story work begins until this phase is complete and green.

### Tests for Foundational Layer

> Write these first and confirm they fail before implementing T013–T025.

- [X] T006 [P] Create `tests/unit/test_kernel_purity.py`: AST-walk every module under `src/docdoc/kernel/` asserting imports resolve only to the stdlib allowlist (`bisect`, `hashlib`, `json`, `math`, `typing`, `dataclasses`, `enum`, `re`, `unicodedata`, `collections`) or `pydantic`; plus a `sys.addaudithook` fixture failing on `open`, `socket.*`, `subprocess.*`, `urllib.*`, `os.system` (research.md R9, FR-020, SC-005)
- [X] T007 [P] Create `tests/unit/test_errors.py`: assert the `DocdocError → KernelError → {SpanError, GeometryError, DocumentInvariantError, MergeError, CapabilityError, IdentityError}` hierarchy and that each carries its structured attributes, not just a message (FR-023)
- [X] T008 [P] Create `tests/unit/test_span.py`: invariants SP-1..SP-3, `shift`, `intersects`, `contains`, zero-length spans, and `SpanError` on `start > end` (FR-004)
- [X] T009 [P] Create `tests/unit/test_geometry.py`: invariants BB-1..BB-4 and GE-1, rejection of non-finite and out-of-range coordinates, `union`/`intersects`, zero-area boxes (FR-005)
- [X] T010 [P] Create `tests/unit/test_identity.py`: `canonical_json` key-order independence, `blob_id_for` stability on identical bytes, `document_id_for` sensitivity to each input, and `IdentityError` on `NaN`/`Infinity`/non-string keys (FR-015..FR-018, research.md R3/R4)
- [X] T011 [P] Create `tests/unit/test_span_index.py`: `tokens_in`/`token_at` results equal a brute-force linear scan over randomized token layouts, including empty index and boundary positions
- [X] T012 [P] Create `tests/unit/test_document_construction.py`: every invariant DOC-1 through DOC-10 raises `DocumentInvariantError` (or `IdentityError` for DOC-9), covering out-of-range, unordered, and overlapping tokens, non-contiguous or non-covering pages, dangling `page_index`, and partial geometry under DOC-8 (FR-007, FR-024, SC-009)

### Implementation for Foundational Layer

- [X] T013 Implement `src/docdoc/kernel/errors.py`: the full hierarchy from data-model.md with structured attributes on each type
- [X] T014 [P] Implement `src/docdoc/kernel/span.py`: `Span` NamedTuple, `Span.create()` validating SP-1, plus `is_empty`, `__len__`, `contains`, `intersects`, `shift`
- [X] T015 [P] Implement `src/docdoc/kernel/geometry.py`: `BBox` and `Geometry` NamedTuples with BB-1..BB-4 validation, `width`, `height`, `area`, `union`, `intersects`
- [X] T016 [P] Implement `src/docdoc/kernel/blob.py`: frozen `BlobRef` with `blob_id` pattern validation and `size_bytes >= 0`; carries no bytes (FR-003)
- [X] T017 [P] Implement `src/docdoc/kernel/identity.py`: `canonical_json` (sorted keys, compact separators, `ensure_ascii=False`, `allow_nan=False`), `blob_id_for`, `options_hash_for`, `document_id_for` using the named-field JSON object of research.md R4 — never string concatenation
- [X] T018 Implement `src/docdoc/kernel/token.py`: `Token` NamedTuple carrying `span`, `geometry`, `source_confidence` and **no `text` field** (research.md "Deviations"); depends on T014, T015
- [X] T019 [P] Implement `src/docdoc/kernel/page.py`: frozen `Page` with PG-1, PG-2 validation
- [X] T020 [P] Implement `src/docdoc/kernel/block.py`: `BlockKind` StrEnum and frozen `Block`; blocks may overlap and are not indexed
- [X] T021 [P] Implement `src/docdoc/kernel/table.py`: frozen `Table` and `TableCell` with TB-1..TB-3 validation
- [X] T022 [P] Implement `src/docdoc/kernel/provenance.py`: frozen `Capabilities` and `IngestProvenance` with `text_layer_used`, and no timestamp field (the kernel cannot read the clock)
- [X] T023 Implement `src/docdoc/kernel/span_index.py`: immutable `SpanIndex` over parallel sorted arrays with `bisect`-based `tokens_in` (O(log n + k)) and `token_at` (research.md R2); depends on T018
- [X] T024 Implement `src/docdoc/kernel/document.py`: frozen `Document` model fields and the single construction validator enforcing DOC-1 through DOC-10, so an invalid document cannot exist; depends on T013–T023
- [X] T025 Populate `src/docdoc/kernel/__init__.py` with exactly the public surface listed in contracts/kernel-api.md — nothing more
- [X] T026 Run `uv run lint-imports` and `uv run pytest tests/unit/test_kernel_purity.py` and confirm both pass against the real `src/docdoc/kernel/` tree

**Checkpoint**: A valid `Document` can be constructed by hand, an invalid one cannot, and the kernel is provably dependency-clean. User stories may now begin.

---

## Phase 3: User Story 1 - Represent a document without losing where anything came from (Priority: P1) 🎯 MVP

**Goal**: Resolve any text range to the page and bounding box it occupies.

**Independent Test**: Construct a document by hand with no files and no network, request the
location of a known text range, and confirm the page and box match the token that produced it.

### Tests for User Story 1

- [X] T027 [P] [US1] Create `tests/unit/test_locate.py`: one `Geometry` per intersecting token in document order (research.md R8); `()` for a zero-length span; `()` for inter-token whitespace; `SpanError` for out-of-range spans with no clamping; `CapabilityError` when `capabilities.geometry` is False; multi-page spans returning entries ordered by page; and rejection of in-place mutation (US1 scenarios 1–5, FR-008, FR-009, FR-022)

### Implementation for User Story 1

- [X] T028 [US1] Implement `Document.locate` in `src/docdoc/kernel/document.py`: intersecting-token lookup via `SpanIndex`, full token boxes with no sub-token interpolation (research.md R7), results ordered by page then token start
- [X] T057 [US1] Implement `Document.page_for` in `src/docdoc/kernel/document.py`: resolve a span to the page indices it falls on, working **without geometry** by using the DOC-5 guarantee that pages tile the text exactly; empty span yields `()`; out-of-range span raises `SpanError` and never `CapabilityError` (FR-025, FR-006)
- [X] T058 [P] [US1] Add `TestPageResolution` to `tests/unit/test_locate.py`: single-page and multi-page spans, resolution on a document whose parser supplied no geometry, empty span, and out-of-range rejection (FR-025)
- [X] T029 [US1] Add the `CapabilityError` guard to `Document.locate` in `src/docdoc/kernel/document.py`, raising when `provenance.capabilities.geometry` is False rather than returning an empty tuple (FR-022, no silent fallback)
- [X] T030 [P] [US1] Create `examples/build_document.py`: a standalone, runnable script constructing a document and locating a value with no infrastructure (SC-007, SC-010)
- [X] T031 [P] [US1] Create `docs/concepts/document.md` explaining the IR, code-point positions, and the token/geometry relationship

**Checkpoint**: US1 is independently functional — quickstart V1 passes end to end.

---

## Phase 4: User Story 2 - Cut a document apart and put it back together (Priority: P2)

**Goal**: `slice` and `merge` that never change where a piece of text physically came from.

**Independent Test**: Cut a document into disjoint parts, reassemble, and confirm a text range
resolves to identical geometry before and after.

### Tests for User Story 2

- [X] T032 [P] [US2] Create `tests/property/strategies.py`: Hypothesis strategies generating documents with random text (including Vietnamese diacritics, combining marks, and non-BMP characters), random page counts, and valid token layouts, plus a strategy producing partitions of a document
- [X] T033 [US2] Create `tests/property/test_document_invariants.py`: the round-trip invariant `locate(s) == merge(partition(d)).locate(remap(s))` over at least 10,000 generated cases covering page boundaries, empty spans, adjacent spans, multi-page spans, single-token and empty documents; plus slice-text equality, geometry bit-stability, and index-vs-brute-force agreement (FR-012, SC-002); depends on T032
- [X] T034 [P] [US2] Create `tests/unit/test_slice.py`: sliced text equality; fully contained tokens retained and rebased; partially covered tokens dropped; geometry unchanged; pages clipped, rebased, and renumbered; empty-span slice yielding an empty document that still carries `source` and `provenance`; `SpanError` out of range (US2 scenarios 1, 4, 5)
- [X] T035 [P] [US2] Create `tests/unit/test_merge.py`: concatenation with no separator; token spans shifted while geometry stays unchanged; page coalescing and renumbering; `merge((d,))` equivalent to `d`; `MergeError` for `mismatched_source`, `overlapping_parts`, and `no_parts` (US2 scenarios 2, 6, FR-013)

### Implementation for User Story 2

- [X] T036 [US2] Implement `Document.slice` in `src/docdoc/kernel/document.py`: retain fully contained tokens rebased by `-span.start`, drop partially covered tokens, keep `Geometry` untouched, clip and renumber pages, remap every `page_index`, carry `source` and `provenance` through
- [X] T037 [US2] Implement `Document.merge` in `src/docdoc/kernel/document.py`: validate shared `blob_id`/`parser_id`/`parser_version` and non-overlapping original ranges, concatenate in order with a running offset, shift token spans while leaving geometry unchanged, coalesce duplicate pages
- [X] T038 [US2] Implement the span remap helper in `src/docdoc/kernel/document.py` that carries a span from an original document into a merged document's coordinate space, used by both `merge` and the property tests
- [X] T059 [US2] Add the `origin` field and invariant DOC-10 to `src/docdoc/kernel/document.py`: record which ranges of the original parse a document occupies, so `merge` can reject overlapping and out-of-order parts. Without it the rejection rules in contracts/kernel-api.md are not implementable (FR-013)
- [X] T039 [US2] Confirm identity behaviour of `slice` and `merge` in `src/docdoc/kernel/document.py`: both re-derive `document_id` from source and provenance, which they do not change, so a derived document carries the **same** id as its parent. `document_id` identifies the parse; `origin` identifies the view (contracts/kernel-api.md)

**Checkpoint**: The foundational invariant holds under property testing. US1 and US2 both work independently.

---

## Phase 5: User Story 3 - Tell two parses of the same file apart (Priority: P3)

**Goal**: Two-level identity, so positions from one parse can never be applied to another.

**Independent Test**: Derive identities for the same bytes under two parse configurations and
confirm the source identity matches while the document identities differ.

> The identity **functions** were built in Phase 2 because `Document` construction (DOC-9) cannot
> exist without them. This phase completes and verifies their **contract**.

### Tests for User Story 3

- [X] T040 [P] [US3] Create `tests/unit/test_identity_contract.py`: same bytes yield the same `blob_id`; two parser ids over one blob yield different `document_id`s while sharing `blob_id`; identical parser, version, and options yield identical `document_id`; options differing only in key order yield identical identity; a `parser_version` bump changes `document_id`. Also assert **FR-017**: a span taken from one parse names different text in another parse of the same bytes, so spans are only meaningful relative to `document_id` (US3 scenarios 1–5, FR-017, SC-003, SC-004)
- [X] T041 [P] [US3] Add adversarial cases to `tests/unit/test_identity.py`: confirm the named-field encoding resists the concatenation collision class — `(parser_id="pdf", version="1.0")` and `(parser_id="pdf1", version=".0")` must produce different `document_id`s (research.md R4)

### Implementation for User Story 3

- [X] T042 [US3] Complete the `IdentityError` paths in `src/docdoc/kernel/identity.py`: reject non-finite floats, non-string dict keys, and non-JSON-primitive values with the offending field named in the error
- [X] T043 [US3] Verify and harden the DOC-9 identity check in `src/docdoc/kernel/document.py` so a `Document` whose `id` does not match its derivation is rejected at construction
- [X] T044 [P] [US3] Create `docs/concepts/identity.md` explaining blob-versus-document identity, why spans anchor to `document_id`, and the collision class the encoding avoids

**Checkpoint**: Parses of one file are distinguishable. Milestone 2 (parsers) is unblocked.

---

## Phase 6: User Story 4 - Find exactly where a piece of text occurs (Priority: P4)

**Goal**: Deterministic exact search, the primitive grounding will build on.

**Independent Test**: Search a constructed document for a string occurring more than once and
confirm all occurrences return in document order.

### Tests for User Story 4

- [X] T045 [P] [US4] Create `tests/unit/test_find.py`: three occurrences returned ascending; absent string returning `()` without error; non-overlapping matches for `"aaa"` in `"aaaaa"` under the documented left-to-right rule; repeated calls returning identical results; `SpanError` on an empty search string; literal matching with no case folding, normalization, or whitespace collapsing (US4 scenarios 1–4, FR-014)

### Implementation for User Story 4

- [X] T046 [US4] Implement `Document.find` in `src/docdoc/kernel/document.py`: exact, non-overlapping, left-to-right scan resuming at `match.end`, with **no `fuzzy` parameter** (ADR-0005)
- [X] T047 [P] [US4] Extend `examples/build_document.py` to demonstrate `find` feeding `locate`, the exact path grounding will follow in Milestone 4

**Checkpoint**: All four user stories are independently functional.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T048 Configure the coverage gate in `pyproject.toml` to fail under 100% statement coverage for `locate`, `find`, `slice`, and `merge` in `src/docdoc/kernel/document.py` (SC-006)
- [X] T049 [P] Create `tests/perf/test_kernel_perf.py` asserting the plan.md targets: construction of a 50k-token document under 250 ms, `locate` p95 under 1 ms, `slice` under 20 ms, `merge` of 100 parts under 50 ms
- [X] T050 Resolve `mypy --strict` findings across `src/docdoc/kernel/` until clean
- [X] T051 [P] Create `README.md` at repository root: what docdoc is, the quickstart from quickstart.md, and an explicit statement of Principles I, II, and XI — this closes the ⚠ pending item in the constitution's Sync Impact Report
- [X] T052 [P] Create `CONTRIBUTING.md` covering the layer rule, the kernel dependency allowlist, and the requirement that kernel changes ship property tests
- [X] T053 [P] Create `CHANGELOG.md` with the `0.1.0` entry describing the kernel surface
- [X] T054 Verify no item from spec.md "Out of Scope" appears in `src/docdoc/kernel/` — no parser, fuzzy matching, normalization, persistence, or serialization code
- [X] T055 Execute every validation scenario V1–V4 in `specs/001-kernel-document-ir/quickstart.md` and confirm the Definition of Done checklist there passes
- [X] T056 [P] Add `LICENSE` at repository root — Apache-2.0, per ADR-0007; `pyproject.toml` declares the SPDX expression and ships the file in built distributions

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: no dependencies
- **Foundational (Phase 2)**: depends on Setup — **blocks every user story**
- **User Stories (Phases 3–6)**: all depend on Foundational
- **Polish (Phase 7)**: depends on the stories being complete

### User Story Dependencies

- **US1 (P1)**: depends only on Foundational. No dependency on other stories.
- **US2 (P2)**: depends on Foundational, and uses `locate` from US1 to *assert* its invariant. `slice`/`merge` themselves do not require US1, but the property test does — implement US1 first.
- **US3 (P3)**: depends only on Foundational. Fully independent of US1 and US2.
- **US4 (P4)**: depends only on Foundational. Fully independent of US1–US3.

### Within Each Story

- Tests are written first and must fail before implementation
- Value primitives before aggregates, aggregates before `Document`, `Document` before its operations
- Tasks touching `src/docdoc/kernel/document.py` are strictly sequential — T028, T029, T036, T037, T038, T039, T046 all edit that one file

### Parallel Opportunities

- Setup: T002, T003, T005
- Foundational tests: T006–T012 all run in parallel (seven distinct files)
- Foundational implementation: T014, T015, T016, T017 in parallel; then T019, T020, T021, T022 in parallel
- US2 tests: T034 and T035 in parallel; T032 before T033
- US3: T040 and T041 in parallel, then T042 and T044 in parallel
- Polish: T049, T051, T052, T053, T056 in parallel
- **Across stories**: once Foundational is done, US3 and US4 can proceed fully in parallel with US1/US2 by different contributors — they touch different test files, and their only shared file is `document.py`

## Parallel Example: Foundational Tests

```bash
# All seven foundational test files are independent — write them together:
Task: "Create tests/unit/test_kernel_purity.py"
Task: "Create tests/unit/test_errors.py"
Task: "Create tests/unit/test_span.py"
Task: "Create tests/unit/test_geometry.py"
Task: "Create tests/unit/test_identity.py"
Task: "Create tests/unit/test_span_index.py"
Task: "Create tests/unit/test_document_construction.py"
```

## Implementation Strategy

### MVP scope

**Phase 1 + Phase 2 + Phase 3 (US1)** — T001 through T031. This delivers a constructible,
provably pure, invariant-enforcing document IR that resolves a text range to a page and box. It is
demonstrable via `examples/build_document.py` with no infrastructure, and it is the smallest
increment with standalone value.

### Incremental delivery

1. Setup + Foundational → an invalid `Document` becomes unconstructable
2. Add US1 → locate works → **MVP, demo via the example script**
3. Add US2 → the round-trip invariant is proven under property testing → the kernel is trustworthy
4. Add US3 → Milestone 2 (parsers) unblocked
5. Add US4 → Milestone 4 (grounding) unblocked

### Recommended stopping point for review

After **T033**. That task proves the invariant the entire product rests on. Constitution Quality
Gate 2 blocks all higher-layer work until it is green, so it is the correct place to pause and
review before building outward.

## Notes

- `[P]` means different files with no incomplete dependency
- Every task touching `document.py` is sequential by construction — plan contributor assignments accordingly
- Commit after each task or logical group
- Verify tests fail before implementing
- **T056 is intentionally blocked** on an unresolved constitutional decision; do not resolve it by guessing a license
