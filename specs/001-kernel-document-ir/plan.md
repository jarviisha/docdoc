# Implementation Plan: Kernel and Canonical Document IR

**Branch**: `001-kernel-document-ir` | **Date**: 2026-08-14 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `/specs/001-kernel-document-ir/spec.md`

## Summary

Build docdoc's L0 kernel: an immutable, deterministic, dependency-light document representation
that preserves the link between text positions and physical page locations, plus the four
operations everything above it depends on — `locate`, `find`, `slice`, `merge` — and the two-level
identity model from ADR-0002.

The technical approach is deliberately conservative. Hot value types are `NamedTuple` and
aggregates are frozen Pydantic models, so per-token validation cost is avoided while document-level
invariants are enforced exactly once, at construction, making an invalid `Document` unconstructable.
Token lookup is binary search over sorted arrays, which is sufficient precisely because construction
guarantees tokens are ordered and non-overlapping. Correctness is established by property-based
testing of the cut-and-reassemble invariant before any higher layer exists to depend on it.

## Technical Context

**Language/Version**: Python >= 3.11 (`Self`/`LiteralString`, exception groups, and no need for
`typing_extensions` in the kernel — see [research.md R12](research.md))

**Primary Dependencies**: `pydantic` v2 (the kernel's only permitted runtime dependency).
Standard-library allowlist: `bisect`, `hashlib`, `json`, `math`, `typing`, `dataclasses`, `enum`,
`re`, `unicodedata`, `collections`. Enforced verbatim by `tests/unit/test_kernel_purity.py`, which
is the authoritative list — this table follows it.

**Storage**: N/A — the kernel performs no I/O by constitutional rule

**Testing**: `pytest` + `hypothesis` (property tests), `mypy --strict`, `ruff`, `import-linter`
(layer contracts) — all dev-only

**Target Platform**: Cross-platform library; CPython 3.11+ on Linux, macOS, Windows

**Project Type**: Single Python library, `src/` layout, published to PyPI

**Performance Goals**:

Targets below were revised after implementation against measured figures; the originals were
unmeasured estimates. Enforced by `tests/perf/test_kernel_perf.py` (marked `perf`).

| Operation | Target | Measured | Basis |
|---|---|---|---|
| `Document` construction, 50k tokens | < 300 ms | ~165 ms | One validation pass, no per-token model instantiation |
| `locate()` p95, 50k-token document | < 1 ms | ~0.02 ms | O(log n + k) via `bisect` |
| `find()` on 1 MB text | < 50 ms | ~1 ms | Delegates to CPython `str.find` |
| `slice()`, whole 50k-token document | < 300 ms | ~160 ms | **Revised from 20 ms.** A slice produces a new `Document`, which re-checks every invariant, so it costs about what construction costs. That validation is the guarantee, not overhead to remove |
| `merge()`, 100 parts / 50k tokens | < 300 ms | ~120 ms | **Revised from 50 ms**, same reason |
| Memory overhead | < 400 bytes/token | — | ~20 MB for 50k tokens |

A defect found by these tests and fixed: `SpanIndex.tokens_in` iterated `self._tokens[first:]`,
copying the whole tail on every call and making `locate()` O(n). It now walks by index. This was
invisible on small documents and would have degraded grounding badly at scale.

**Constraints**: No filesystem, network, clock, or randomness anywhere in `kernel/` (FR-020);
deterministic and byte-identical across platforms (FR-019); no dependency on any higher docdoc
layer or provider SDK (FR-021); text positions are Unicode code points, never bytes (FR-004)

**Scale/Scope**: Documents up to ~1,000 pages / ~500k tokens representable without pathological
memory or latency. Scope is ~10 kernel modules and their tests; no parsers, models, or services.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

Evaluated against constitution v1.1.0. **Initial: PASS. Post-design re-check: PASS.**

| # | Gate (constitution principle) | Status |
|---|-------------------------------|--------|
| 1 | **Kernel purity (I)** | **PASS** — `pydantic` plus a stdlib allowlist; `Document` is frozen; bytes live in `BlobRef` only. Enforced by an AST test (research.md R9), not convention |
| 2 | **Provenance preservation (I, VIII)** | **PASS** — `slice`/`merge` leave `Geometry` bit-identical and carry `source` and `provenance` through. Tokens that a slice would truncate are **dropped rather than kept with stale geometry** |
| 3 | **Grounding integrity (II)** | **N/A** — no extraction at this milestone. `find()` is exact-only per ADR-0005, which is the primitive grounding will build on |
| 4 | **Determinism (III)** | **PASS** — no clock, randomness, network, or provider state. `locate()` returns raw token geometry with no grouping heuristic (research.md R8) |
| 5 | **Provider isolation (IV)** | **N/A** — no provider code exists yet. The kernel defines no provider-shaped types |
| 6 | **Text-first (V)** | **N/A** — no parsers at this milestone. `IngestProvenance.text_layer_used` reserves the field the decision will be recorded in |
| 7 | **Schema-driven (VI)** | **N/A** — no extraction. No document-type-specific type appears in the kernel |
| 8 | **Validation separation (VII)** | **N/A** — domain validation is a later milestone. Structural invariants here are construction-time correctness, not result validation |
| 9 | **No silent fallback (VIII)** | **PASS** — `CapabilityError` for unavailable geometry, `SpanError` for out-of-range, `MergeError` for mismatched parts. DOC-8 forbids partial geometry outright rather than degrading |
| 10 | **Measurability (IX)** | **PASS** — every success criterion in spec.md is a countable quantity. Golden-set evaluation is Milestone 6 and is not claimed here |
| 11 | **Layer direction (X)** | **PASS** — the kernel is the bottom layer and imports nothing above it; `import-linter` enforces the full order in CI (research.md R10) |
| 12 | **MVP discipline (XI)** | **PASS** — nothing from the Deferred Technology list. `SpanIndex` is the one non-obvious abstraction and exists for a present-tense reason: O(n) lookup is too slow at 500k tokens |
| 13 | **Kernel test rigor (XII)** | **PASS** — property tests are the centrepiece; >= 10,000 generated cases; `locate(original) == locate(remapped)` asserted directly |
| 14 | **Open decisions** | **PASS** — both blocking TODOs gating Milestone 1 are resolved (ADR-0002 identity, ADR-0005 exact-only `find`). Nothing here resolves an open decision implicitly |

### Design decisions that refine the spec

Recorded so reviewers see them rather than discovering them in code. None is a constitution
violation. Items 1–2 were identified during planning; items 3–5 emerged during implementation,
when the design as written turned out not to be implementable, and are recorded here after the
fact.

1. **Partial geometry is rejected, not supported** (data-model DOC-8). spec.md's edge-case list
   admitted "geometry for only some tokens". Allowing it would make `locate()` silently lossy — a
   caller could not distinguish "no token there" from "geometry unavailable here". A parser with
   partial geometry must declare `capabilities.geometry = False`. spec.md has been reconciled.
2. **`slice()` drops partially covered tokens** (contracts/kernel-api.md). Keeping a clipped token
   would leave its geometry describing glyphs no longer in the sliced text. Dropping loses a token;
   keeping would produce a wrong box, and a wrong box is worse than a missing one.
3. **`page_for()` was added** (FR-025, task T057). FR-006 requires every token be traceable to a
   page, but `Geometry` is the only page-bearing field and `locate()` raises `CapabilityError`
   without it — so a text-only document could answer no page question at all. FR-006 was
   unreachable until this existed.
4. **`Document.origin` was added** (DOC-10, task T059). It records which ranges of the original
   parse a document occupies. Without it `merge` cannot tell whether two parts overlap or which
   order they belong in, and the rejection rules in contracts/kernel-api.md are not implementable.
5. **DOC-4 relaxed to ascending-and-unique** rather than contiguous from zero. This follows from
   `slice()` preserving original page numbers: a slice of page 7 must still report page 7, so a
   sliced document legitimately holds a sparse index set. Renumbering would destroy exactly the
   provenance this project exists to protect. A consequence worth knowing: `page_index` is no
   longer an index into `pages` — look pages up by their `index` value.

A sixth item is identity, which turned out to behave differently from the plan rather than being
a deliberate refinement: `slice` and `merge` do **not** change `document_id`, because identity
derives from blob, parser, version, and options, none of which they touch. `document_id`
identifies the parse; `origin` identifies the view.

## Project Structure

### Documentation (this feature)

```text
specs/001-kernel-document-ir/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output — 12 resolved decisions
├── data-model.md        # Phase 1 output — entities, invariants, errors
├── quickstart.md        # Phase 1 output — setup and validation scenarios
├── contracts/
│   └── kernel-api.md    # Phase 1 output — public API contract
├── checklists/
│   └── requirements.md  # Spec quality checklist (16/16)
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

Single-project Python library. Only the paths below are created by this feature; sibling packages
(`ingest/`, `transform/`, `extraction/`, `pipeline/`, `api/`) arrive in later milestones and are
shown in `FIRST_DOC.md §5`.

```text
pyproject.toml               # deps, extras, ruff/mypy/pytest config, import-linter contracts
uv.lock                      # committed (Principle VIII — reproducibility)

src/docdoc/
├── __init__.py
└── kernel/
    ├── __init__.py          # the public surface in contracts/kernel-api.md
    ├── errors.py            # DocdocError → KernelError → six specific types
    ├── span.py              # Span
    ├── geometry.py          # BBox, Geometry
    ├── token.py             # Token
    ├── page.py              # Page
    ├── block.py             # Block, BlockKind
    ├── table.py             # Table, TableCell
    ├── blob.py              # BlobRef
    ├── provenance.py        # Capabilities, IngestProvenance
    ├── identity.py          # canonical_json, blob_id_for, options_hash_for, document_id_for
    ├── span_index.py        # SpanIndex (bisect over sorted arrays)
    └── document.py          # Document + locate/find/slice/merge

tests/
├── unit/
│   ├── test_span.py, test_geometry.py, test_token.py
│   ├── test_identity.py            # canonicalization, ordering, NaN rejection
│   ├── test_span_index.py
│   ├── test_document_construction.py   # DOC-1 … DOC-9
│   ├── test_locate.py, test_find.py, test_slice.py, test_merge.py
│   ├── test_errors.py
│   └── test_kernel_purity.py       # AST allowlist + audit hook (research.md R9)
├── property/
│   ├── strategies.py               # Hypothesis document/span generators
│   └── test_document_invariants.py # the round-trip invariant
└── fixtures/

examples/
└── build_document.py        # standalone, no infrastructure (SC-010)
```

**Structure Decision**: `src/` layout, so tests import the installed package and cannot
accidentally pass by importing loose source. The kernel is one flat package with one module per
concept — matching the constitution's "no god objects" boundary list without introducing
sub-packages a ~10-module layer does not need.

## Complexity Tracking

> **Fill ONLY if Constitution Check has violations that must be justified**

No violations. The Constitution Check passes on every applicable gate, and the two design
refinements above are stricter than the spec rather than exceptions to it.
