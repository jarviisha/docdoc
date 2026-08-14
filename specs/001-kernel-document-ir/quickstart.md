# Quickstart & Validation Guide: Kernel and Canonical Document IR

**Feature**: `001-kernel-document-ir` | **Date**: 2026-08-14

How to set up, run, and validate this feature. Entity definitions live in
[data-model.md](data-model.md); operation semantics live in
[contracts/kernel-api.md](contracts/kernel-api.md). This document does not repeat them.

---

## Prerequisites

- Python >= 3.11
- [`uv`](https://docs.astral.sh/uv/)
- **Nothing else.** No database, no object storage, no credentials, no network (SC-007). If any
  step here needs one of those, this feature has a defect.

## Setup

```bash
uv sync --all-extras     # installs pydantic + dev tooling; uv.lock is committed
```

## Run the suites

```bash
uv run pytest tests/unit tests/property     # kernel suites
uv run pytest tests/property -p no:randomly # determinism: identical results run to run
uv run mypy --strict src/docdoc/kernel
uv run ruff check . && uv run ruff format --check .
uv run lint-imports                         # layer-direction contract (research.md R10)
```

Expected: everything passes, offline, in well under a minute.

---

## Validation scenarios

Each maps to a user story in [spec.md](spec.md). Together they demonstrate the feature end to end.

### V1 — Build a document and locate a value (US1, P1)

**Goal**: prove a text range resolves to a page and a box, with no parser and no infrastructure.

1. Construct a `Document` by hand: text containing `"INV-001"`, one `Page`, tokens with geometry,
   an `IngestProvenance` declaring `capabilities.geometry = True`, and a `BlobRef`.
2. Call `doc.find("INV-001")` → exactly one `Span`.
3. Call `doc.locate(span)` → `Geometry` values whose `page_index` is 0 and whose boxes lie within
   `0.0..1.0`.

**Pass**: the returned box matches the token that produced the text.

**Also verify**:
- `doc.locate(Span(5, 5))` → `()` (empty range, not an error).
- `doc.locate(Span(0, len(doc.text) + 1))` → `SpanError`, not a clamped result.
- A document built with `capabilities.geometry = False` → `locate()` raises `CapabilityError`,
  never an empty tuple (FR-022).
- Attempting to assign to any field → the model rejects it.

### V2 — Cut and reassemble without losing provenance (US2, P2)

**Goal**: the foundational invariant.

1. Build a multi-page document with tokens on each page.
2. Record `doc.locate(s)` for a chosen span `s`.
3. Partition the document into disjoint slices covering it entirely.
4. `Document.merge(parts)`, remap `s` into the merged coordinate space, and locate again.

**Pass**: the two geometry tuples are **equal**, including `page_index` (FR-012).

**Also verify**:
- Slicing a span crossing a page boundary keeps both pages and their token associations.
- Slicing an empty span yields an empty document that still carries `source` and `provenance`.
- Merging parts from different `blob_id`s → `MergeError(reason="mismatched_source")`.
- Merging overlapping parts → `MergeError(reason="overlapping_parts")`.
- `Document.merge(())` → `MergeError(reason="no_parts")`.

### V3 — Distinguish two parses of one file (US3, P3)

1. `blob_id_for(data)` twice on the same bytes → identical.
2. `document_id_for(...)` with `parser_id="pdf_text"` vs `"cloud_di"`, same blob → **different**.
3. Same parser, version, and options → identical.
4. `options_hash_for({"a": 1, "b": 2})` vs `{"b": 2, "a": 1}` → identical (SC-004).
5. Bump `parser_version` → different `document_id`.
6. `options_hash_for({"x": float("nan")})` → `IdentityError`.

### V4 — Exact search (US4, P4)

1. A string occurring three times → three spans, ascending, non-overlapping.
2. An absent string → `()`, no error.
3. `doc.find("")` → `SpanError`.
4. Overlapping candidates (`"aaa"` in `"aaaaa"`) → non-overlapping matches per the documented
   left-to-right rule.

---

## Property tests (SC-002)

The core of this milestone. In `tests/property/test_document_invariants.py`, using Hypothesis
strategies that generate documents with random text, page counts, and token layouts:

| Property | Assertion |
|---|---|
| Round trip | `locate(s) == merge(partition(d)).locate(remap(s))` for every span `s` |
| Slice text | `d.slice(s).text == d.text[s.start:s.end]` |
| Geometry stability | Retained tokens' geometry is bit-identical before and after slice/merge |
| Identity determinism | Same inputs → same `document_id`, across repeated derivation |
| Index agreement | `SpanIndex.tokens_in(s)` equals a brute-force linear scan |
| Construction rejection | Malformed token sets always raise `DocumentInvariantError` |

**Coverage requirement**: page boundaries, empty spans, adjacent spans, multi-page spans,
single-token documents, empty documents, and non-ASCII text (Vietnamese diacritics, combining
marks, and characters outside the BMP).

**Budget**: at least 10,000 generated cases across the suite, zero failures. Any Hypothesis
falsifying example is committed to the `.hypothesis` example database and pinned as a regression
test.

---

## Determinism and purity checks (SC-005)

```bash
uv run pytest tests/unit/test_kernel_purity.py
```

Asserts, per [research.md R9](research.md):
- Every module under `kernel/` imports only the stdlib allowlist or `pydantic` (AST scan).
- No file, socket, or subprocess audit event fires during the kernel suite.
- Running the suite twice produces identical results.

---

## Definition of done

- [ ] All four validation scenarios pass.
- [ ] Property suite green at >= 10,000 cases, zero failures.
- [ ] 100% statement coverage of `locate`, `find`, `slice`, `merge` (SC-006).
- [ ] Purity and import-boundary checks pass in CI.
- [ ] `mypy --strict` clean on `src/docdoc/kernel`.
- [ ] `examples/build_document.py` runs standalone and reproduces V1 (SC-010).
- [ ] No entry from spec.md's "Out of Scope" appears anywhere in `src/docdoc/kernel`.
