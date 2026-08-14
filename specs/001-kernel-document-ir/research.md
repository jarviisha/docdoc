# Phase 0 Research: Kernel and Canonical Document IR

**Feature**: `001-kernel-document-ir` | **Date**: 2026-08-14

Each item below resolves an unknown in the plan's Technical Context. Decisions here are binding
for Phase 1 design. Where a decision departs from `FIRST_DOC.md`, the departure is called out
explicitly.

---

## R1. Runtime type strategy: immutability without a validation tax

**Decision**: Two tiers.

- **Hot primitives** — `Span`, `BBox`, `Geometry`, `Token` — are `typing.NamedTuple`. Immutable,
  tuple-cheap, zero per-instance validation cost, and free structural equality/ordering.
- **Aggregates** — `Document`, `Page`, `Block`, `Table`, `BlobRef`, `IngestProvenance`,
  `Capabilities` — are frozen Pydantic models. Validated once at construction, where the cost is
  paid per document rather than per token.

**Rationale**: A 500-page scan carries ~500k tokens. Pydantic validates roughly 10⁵–10⁶ simple
models/sec, so per-token validation would cost seconds per document and dominate the whole kernel.
NamedTuple construction is a tuple allocation. Invariants that matter for tokens (ordering, bounds,
non-overlap) are collection-level properties anyway, so they are enforced once in `Document`
construction (FR-007) rather than per token — which is both faster and the only place they are
checkable.

**Alternatives considered**:

- *All Pydantic*: uniform, but pays validation per token for invariants Pydantic cannot express
  (a token's bounds are only valid relative to sibling tokens and document text).
- *All frozen dataclasses with `slots=True`*: ~as fast as NamedTuple, lower memory for large field
  counts, but loses tuple unpacking and free ordering, and still needs manual `__eq__` care.
  NamedTuple wins for 2–4 field value objects.
- *`attrs`*: adds a runtime dependency the constitution forbids in the kernel.

---

## R2. Span index: how `locate()` finds covering tokens

**Decision**: Sorted arrays plus `bisect` from the standard library. `SpanIndex` holds tokens
ordered by `span.start`, with a parallel array of start offsets. Lookup is
`bisect_right(starts, query.start) - 1`, then a forward walk while `token.span.start < query.end`.

**Rationale**: FR-007 guarantees tokens are ascending and non-overlapping, which reduces the
general interval-stabbing problem to binary search — O(log n + k) for k intersecting tokens, with
no dependency and no tree to keep balanced. An interval tree only earns its complexity when
intervals overlap, which construction forbids.

**Alternatives considered**:

- *Interval tree / segment tree*: solves a problem this data model does not have.
- *Linear scan*: O(n) per lookup; at 500k tokens and many lookups per document during grounding,
  this becomes the bottleneck.
- *`numpy` searchsorted*: faster in bulk, but a forbidden kernel dependency.

---

## R3. Canonical serialization of options (FR-018)

**Decision**: Options must be a JSON-primitive tree (`str`, `int`, `float`, `bool`, `None`, `list`,
`dict` with `str` keys). Canonical form is:

```python
json.dumps(
    options, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
).encode("utf-8")
```

Non-finite floats (`NaN`, `±Infinity`) and non-`str` dict keys are rejected with an explicit error
at construction, not silently coerced.

**Rationale**: `sort_keys` removes ordering sensitivity (spec scenario US3-4). `separators` removes
whitespace variance. `allow_nan=False` blocks the two values that break both JSON interop and hash
stability. CPython's `float.__repr__` has produced the shortest round-tripping representation since
3.1 and is platform-independent for IEEE-754 doubles, so float formatting is stable across the
machines this must agree on.

**Risk accepted and mitigated**: floats in options are still a hazard if a caller passes a computed
value whose last bit differs across platforms. Mitigation is documentation plus a strong
recommendation to keep options to strings, ints, and bools; this is not machine-enforced because a
legitimate float option (a DPI or a threshold) is plausible.

**Alternatives considered**: *Canonical JSON (RFC 8785/JCS)* — stricter and a real standard, but
its number canonicalization needs either a dependency or a nontrivial implementation, for a
property plain `json.dumps` already delivers for our input domain. *CBOR/msgpack* — deterministic
encodings exist but require a dependency.

---

## R4. Identity derivation (FR-015, FR-016)

**Decision**:

```text
blob_id     = "sha256:" + sha256(original_bytes).hexdigest()
document_id = "sha256:" + sha256(canonical_json({
                  "v": 1,
                  "blob_id": blob_id,
                  "parser_id": parser_id,
                  "parser_version": parser_version,
                  "options_hash": options_hash,
              })).hexdigest()
```

Inputs are hashed as a **canonical JSON object with named fields**, never as concatenated strings.
A schema version `v` is embedded so the derivation itself can evolve.

**Rationale**: Naive concatenation is ambiguous — `parser_id="pdf"` + `version="1.0"` and
`parser_id="pdf1"` + `version=".0"` produce identical input and therefore identical identity. A
structured, length-delimited encoding removes the collision class entirely. This is a correction to
the concatenation formula sketched in `FIRST_DOC.md §15`; ADR-0002's intent is preserved exactly,
only the encoding is made unambiguous.

**Alternatives considered**: *BLAKE3/BLAKE2b* — faster, but SHA-256 is the constitutional and
ecosystem default, is hardware-accelerated on modern CPUs, and hashing is not the bottleneck.
*Concatenation with separators* — works only until a separator appears in a value.

---

## R5. Text position units (FR-004)

**Decision**: Positions are Python string indices, i.e. **Unicode code points**, not bytes and not
grapheme clusters. Documented in the public contract.

**Rationale**: Byte offsets vary with encoding and make slicing incorrect for non-ASCII text, which
is disqualifying for a project whose first target documents include Vietnamese. Grapheme clusters
would need a Unicode segmentation dependency and do not match how any parser reports positions.
Code points are what CPython gives natively, so `text[span.start:span.end]` is correct by
construction. CPython's flexible string representation means no surrogate-pair hazard.

---

## R6. `merge()` semantics — the sharpest design question

**Decision**: `merge()` concatenates parts **in the given order with no separator**, rebasing all
positions by a running offset. It requires that every part shares the same `blob_id`, `parser_id`,
and `parser_version` (FR-013), and rejects parts whose original ranges **overlap**. Non-adjacent
parts are permitted, and the result records that it is a partial reconstruction.

The FR-012 round-trip invariant is guaranteed for **partitions of one document** — cut into
disjoint pieces, reassembled in original order. That is exactly the property the property tests
assert.

**Rationale**: The demanding case is non-adjacent parts: merging characters 0–100 with 500–600
produces text that never existed as a contiguous run in the source. Two honest options are to
forbid it or to permit it while keeping every token's geometry pointing at its true original
location. Forbidding it would block the intended downstream use — page-parallel processing and
windowed extraction, where a caller legitimately reassembles selected regions. Permitting it while
preserving per-token page and geometry keeps provenance intact, which is what the constitution
actually protects; the merged *text* is a working buffer, while the *geometry* remains true.
Overlapping parts are rejected because they would duplicate tokens and break the non-overlap
invariant that R2's index depends on.

**Amended during implementation.** Two things this decision assumed turned out to be missing:

- **Detecting overlap needs recorded origins.** A `Document` carried no memory of where it came
  from, so `merge` had no way to tell whether two parts described the same region — making the
  rejection rule above unimplementable as written. `Document.origin` (DOC-10) records the ranges of
  the original parse a document occupies. It also lets `merge` require ascending original order,
  without which pages could not be coalesced in reading order.
- **Page numbers are preserved, not renumbered.** Renumbering pages contiguously would make a slice
  of page 7 report "page 0". DOC-4 was relaxed to ascending-and-unique so that original page
  numbers survive both `slice` and `merge`.

A consequence: because merged text is a direct concatenation, a page's contributions from several
parts are contiguous in the result even when those parts were not adjacent in the source. That is
what keeps page spans tiling correctly after a non-adjacent merge.

**Alternatives considered**: *Require adjacency* — simplest and safest, but forecloses windowed
extraction, which is a concrete near-term need rather than a speculative one. *Insert a separator
between non-adjacent parts* — corrupts positions relative to the source and invents characters;
rejected outright.

---

## R7. Sub-token geometry in `locate()`

**Decision**: `locate()` returns geometry for every token **intersecting** the query range, using
each token's full bounding box. No sub-token interpolation. A range covering half a token yields
that token's whole box.

**Rationale**: Parsers report geometry per token, not per character. Interpolating a partial box
from a character offset would require assuming uniform glyph advance — false for proportional
fonts, kerning, and every right-to-left or complex script. That is a heuristic producing
confidently wrong boxes, exactly what Principle III forbids in the kernel. Returning the containing
token is honest and never wrong, only coarse.

---

## R8. `locate()` result shape

**Decision**: One `Geometry` per intersecting token, in document order. The kernel performs **no**
grouping, union, or line detection.

**Rationale**: Merging boxes into per-line or per-page rectangles requires a vertical-overlap
heuristic with a threshold — a tunable, versionable parameter that has no place in the deterministic
kernel. Union-per-page is worse: a multi-line span becomes one large rectangle covering unrelated
text, which is misleading in exactly the audit context this project exists to serve. Token-level
output is lossless; any consumer that wants line grouping can compute it in a presentation layer
where a heuristic is appropriate.

**Consequence**: `locate()` output can be large for long ranges. Acceptable — callers ground short
field values, not whole pages.

**Amended during implementation.** This decision left a caller with no way to ask *which page* a
span is on without also asking for boxes — and `locate()` refuses to answer at all when the parser
supplied no geometry. `page_for()` was added as a separate operation: it derives pages from text
position via the DOC-5 tiling guarantee, so it works with or without geometry, and never raises
`CapabilityError`.

---

## R9. Mechanically proving "no I/O, no clock, no randomness" (FR-020, SC-005)

**Decision**: Two complementary checks, both dev-time only.

1. **Static import check** — a test walks the AST of every module under `kernel/` and asserts that
   every `import` resolves to either the standard-library allowlist (`bisect`, `hashlib`, `json`,
   `math`, `collections`,
   `typing`, `dataclasses`, `enum`, `re`, `unicodedata`) or `pydantic`. Catches forbidden imports
   including those inside functions.
2. **Runtime audit hook** — a pytest fixture installs `sys.addaudithook` for the kernel suite and
   fails on `open`, `socket.*`, `subprocess.*`, `urllib.*`, and `os.system` events.

Clock and randomness are covered by the static check (importing `time`, `datetime`, `random`,
`secrets`, `uuid` is simply not on the allowlist) because `sys.audit` emits no events for them.

**Rationale**: Neither check alone suffices. Audit hooks miss the clock; static analysis misses
I/O reached through an allowed module. Together they cover the stated requirement, using only the
standard library.

---

## R10. Enforcing the layer dependency direction (FR-021)

**Decision**: `import-linter` as a dev dependency, with a `layers` contract declaring
`api > pipeline > extraction > transform > ingest > kernel`, plus a `forbidden` contract barring
provider SDK imports outside `adapters/`. Runs in CI as a required check.

**Rationale**: `import-linter`'s `layers` contract expresses the constitution's dependency order
directly, so the config file reads as the rule it enforces. Hand-rolling this is possible but would
reimplement its module-graph resolution. It is dev-only, so the runtime dependency rule holds.

**Alternatives considered**: *Custom AST test* — already being written for R1's allowlist; extending
it to full transitive layer analysis would duplicate a solved problem. *No enforcement* — the
constitution requires it be machine-checked, not conventional.

---

## R11. Error hierarchy

**Decision**: A single `DocdocError` root, with `KernelError` beneath it, and specific types under
that: `SpanError` (bounds, ordering), `GeometryError` (invalid coordinates), `DocumentInvariantError`
(construction-time token violations), `MergeError` (mismatched or overlapping parts),
`CapabilityError` (requested capability unavailable), `IdentityError` (non-canonicalizable options).

Every error carries structured attributes — the offending span, the document id, the capability
name — not just a formatted message.

**Rationale**: FR-022 and FR-023 require errors that name the offending input. Structured
attributes let higher layers translate to API responses without parsing strings. A single root
lets consumers catch everything docdoc raises. `CapabilityError` is the mechanism enforcing the
constitution's no-silent-fallback rule.

---

## R12. Toolchain and supported Python versions

**Decision**: Python **>= 3.11**. `uv` for environment and lock management, with `uv.lock`
committed. `pytest` + `hypothesis` for tests, `ruff` for lint and format, `mypy --strict` for the
kernel package.

**Rationale**: 3.11 provides `Self`, `LiteralString`, exception groups, and a 10–25% interpreter
speedup, and lets the kernel avoid `typing_extensions` — which would otherwise be a second runtime
dependency, arriving transitively through Pydantic and blurring a boundary the constitution draws
sharply. 3.11 reached wide distribution availability well before this project starts, so requiring
it costs little adoption. `mypy --strict` on the kernel is affordable because the kernel is small
and its types are its contract.

**Alternatives considered**: *3.10* — would broaden reach slightly at the cost of
`typing_extensions` in the kernel. *3.12+* — no feature this feature needs, and narrows adoption
for no gain.

---

## Deviations from `FIRST_DOC.md`

| Topic | `FIRST_DOC.md` | This plan | Why |
|---|---|---|---|
| `Token.text` | Token stores its own `text: str` | Token stores no text; it is derived via `document.text[span]` | Removes a duplicate-state invariant (`token.text == text[span]`) that could drift, and cuts memory materially at 500k tokens |
| Identity input encoding | Concatenated fields | Canonical JSON object of named fields | Concatenation is ambiguous and admits collisions (R4) |
| `find(text, fuzzy=False)` | Fuzzy flag on the kernel operation | Exact only; no `fuzzy` parameter | ADR-0005 — the kernel cannot host fuzzy matching without breaking its dependency rule |
| Kernel dependency | `pydantic` | `pydantic` for aggregates only; hot primitives use the standard library | Same dependency set, better constant factors (R1) |

Four further deviations emerged during implementation rather than planning, when the design as
written proved not to be implementable:

| Topic | Planned | Implemented | Why |
|---|---|---|---|
| Page numbering under `slice`/`merge` | Renumbered contiguously from 0 | Original page numbers preserved; DOC-4 relaxed to ascending-and-unique | A slice of page 7 reporting "page 0" destroys the provenance the project exists to protect |
| Slice/merge identity | `result.id` recomputed as a distinct identity | `result.id` is unchanged; `origin` distinguishes views | Identity derives from blob, parser, version, and options — none of which slicing or merging touches. `document_id` identifies the parse, not one view of it |
| Overlap detection in `merge` | Assumed possible from the parts alone | Requires `Document.origin` (DOC-10) | A document carried no memory of where it came from, so overlap was undetectable (R6) |
| Page lookup | Only via `Geometry` | `page_for()` added | FR-006 requires page traceability, but `locate()` raises without geometry, leaving text-only documents unable to answer (R8) |
