# Contract: Public Kernel API

**Feature**: `001-kernel-document-ir` | **Stability**: pre-1.0, may change; changes require a
`CHANGELOG` entry and a minor version bump

For a library, the public API surface *is* the external contract. Everything below is importable
from `docdoc.kernel`. Anything not listed is private and may change without notice.

---

## Exported surface

```python
from docdoc.kernel import (
    Span,
    BBox,
    Geometry,
    Token,  # value primitives
    Page,
    Block,
    BlockKind,
    Table,
    TableCell,  # structure
    Document,
    SpanIndex,
    BlobRef,  # root and index
    IngestProvenance,
    Capabilities,  # provenance
    blob_id_for,
    canonical_json,
    document_id_for,
    options_hash_for,  # identity
    DocdocError,
    KernelError,
    SpanError,
    GeometryError,  # errors
    DocumentInvariantError,
    MergeError,
    CapabilityError,
    IdentityError,
)
```

---

## `Document.locate`

```python
def locate(self, span: Span) -> tuple[Geometry, ...]:
```

Resolve a text range to the physical locations it occupies.

**Preconditions**: `0 <= span.start <= span.end <= len(self.text)`.

**Postconditions**:

- Returns one `Geometry` per token **intersecting** `span`, in document order
  (ascending `page_index`, then ascending token start). No grouping, union, or interpolation
  ([research.md R7, R8](../research.md)).
- A token intersects when `token.span.start < span.end and token.span.end > span.start`.
- Returns `()` for a zero-length span (FR-009).
- Returns `()` when the range covers text no token claims — for example inter-token whitespace.
- Pure: no mutation, no I/O; equal inputs give equal outputs.

**Errors**:

| Condition | Error |
|---|---|
| `span.end > len(text)` or `span.start < 0` or `start > end` | `SpanError` |
| `self.provenance.capabilities.geometry is False` | `CapabilityError(capability="geometry", available=False)` |

**Complexity**: O(log n + k).

```python
geoms = doc.locate(Span(128, 135))
# (Geometry(page_index=0, bbox=BBox(0.72, 0.11, 0.91, 0.14)),)
```

---

## `Document.page_for`

```python
def page_for(self, span: Span) -> tuple[int, ...]:
```

Resolve a text range to the page indices it falls on, ascending.

**Postconditions**:

- Returns every page whose span intersects `span`, by `index` value.
- Returns `()` for a zero-length span, which occupies no positions and therefore no pages.
- **Works when the parser supplied no geometry.** Pages tile the text exactly (DOC-5), so a span's
  pages are determined by text position alone.
- Pure and deterministic.

**Errors**: `SpanError` when the span does not fit this document's text. Unlike `locate`, this
never raises `CapabilityError` — page resolution needs no geometry capability.

> **Why this exists.** FR-006 requires every token be traceable to a page, but `Geometry` is the
> only page-bearing field and `locate()` refuses to answer without it. A text-only document could
> therefore answer no page question at all. `page_for` closes that hole and is the correct call
> whenever you want the page but not the box.

---

## `Document.find`

```python
def find(self, text: str) -> tuple[Span, ...]:
```

Find every exact occurrence of `text`. **Exact matching only** — no fuzzy parameter exists at this
layer (ADR-0005).

**Postconditions**:

- Returns all **non-overlapping** occurrences, scanning left to right and resuming at
  `match.end` after each hit, in ascending order.
- Returns `()` when absent — a normal outcome, not an error (FR-014).
- Deterministic: repeated calls return identical results.
- Matching is literal, over the document's canonical text. No case folding, no Unicode
  normalization, no whitespace collapsing — those belong to the match view (ADR-0006).

**Errors**: empty `text` raises `SpanError` rather than returning every position (US4-4).

**Complexity**: O(n·m) worst case, delegated to CPython's `str.find`.

---

## `Document.slice`

```python
def slice(self, span: Span) -> Document:
```

Produce a new document covering `span`.

**Postconditions**:

- `result.text == self.text[span.start:span.end]`.
- Tokens **fully contained** in `span` are retained, rebased by `-span.start`. Partially
  overlapping tokens are **dropped**, because a token whose text is truncated no longer
  corresponds to the geometry it carries.
- Retained tokens keep their original `Geometry` unchanged — geometry is page-absolute and
  unaffected by text rebasing. This is what makes the round-trip invariant hold.
- Pages intersecting `span` are retained with spans clipped and rebased. **`index` values are
  preserved, not renumbered** — a slice of page 7 still reports page 7. Page indices are therefore
  strictly ascending rather than contiguous from 0 (DOC-4), and `page_index` references on tokens,
  blocks, and tables continue to resolve unchanged.
- Blocks and tables fully contained in `span` are retained and rebased; partial ones are dropped.
- `result.source` and `result.provenance` are carried over unchanged.
- `result.origin` records which ranges of the *original* parse the slice covers, composing across
  nested slices: `d.slice(a).slice(b).origin` refers back to `d`'s coordinates.
- An empty span yields an empty document that still carries `source` and `provenance` (US2-5).

**Errors**: out-of-range span raises `SpanError`.

> **Page numbers are preserved deliberately.** Renumbering would make a slice of page 7 report
> "page 0", destroying exactly the provenance this project exists to protect. The cost is that
> page indices are no longer an index into `pages` — look pages up by their `index` value.

> **`result.id` is unchanged.** Identity derives from blob, parser, version, and options
> (ADR-0002), none of which slicing touches, so a slice carries the same `document_id` as its
> parent. `document_id` identifies **the parse**, not one particular view of it. What distinguishes
> views is `origin`. Callers needing to tell two views apart must compare `origin`, not `id`.

> **Dropping partial tokens is deliberate.** Retaining a token whose span is clipped would leave
> its geometry describing glyphs no longer present in the sliced text — a silently wrong box.
> Callers wanting whole-token boundaries should expand their span first; a future
> `expand_to_tokens(span)` helper is a natural addition but is out of scope here.

---

## `Document.merge`

```python
@classmethod
def merge(cls, parts: Sequence[Document]) -> Document:
```

Reassemble parts into one document, rebasing positions.

**Preconditions**:

- `parts` is non-empty.
- All parts share the same `source.blob_id`, `provenance.parser_id`, and
  `provenance.parser_version` (FR-013).
- Parts' `origin` ranges do not overlap, and are supplied in ascending original order
  ([research.md R6](../research.md)).

**Postconditions**:

- `result.text` is the parts' texts concatenated in the given order, with no separator.
- Token spans are shifted by the running offset of their part; **geometry is unchanged**, so every
  token still resolves to its true original page and box.
- Pages are **coalesced by `index`**, preserving original page numbers. A page appearing in more
  than one part becomes a single page spanning its combined contributions. Because parts arrive in
  ascending original order and the merged text is a direct concatenation, those contributions are
  contiguous in the result even when the parts themselves were not adjacent in the source.
- `result.origin` is the concatenation of the parts' `origin` ranges.
- `merge((d,))` returns a document equal to `d` in text, tokens, and geometry.
- `result.id` equals the parts' shared `document_id` — merging does not change blob, parser,
  version, or options. See the note under `slice`.

**Errors**:

| Condition | Error |
|---|---|
| `parts` is empty | `MergeError(reason="no_parts")` |
| Parts differ in `blob_id`, `parser_id`, or `parser_version` | `MergeError(reason="mismatched_source")` |
| Parts' original ranges overlap | `MergeError(reason="overlapping_parts")` |
| Parts are supplied out of original order | `MergeError(reason="unordered_parts")` |

> **`merge(())` raises rather than returning an empty document.** With no parts there is no
> `source` and no `provenance` to carry, and a document without provenance is exactly what the
> constitution forbids.

> **Non-adjacent parts are permitted.** The merged *text* is then a working buffer that never
> existed contiguously in the source, while the *geometry* stays true to the original. That trade
> is what keeps windowed extraction possible (research.md R6). Overlapping parts are rejected,
> because they would duplicate tokens and break the non-overlap invariant `SpanIndex` relies on.

---

## The round-trip invariant

The property all other guarantees rest on (FR-012, SC-002):

> For any document `d` partitioned into disjoint, ordered slices `p₁…pₙ` whose cut points do not
> fall strictly inside a token, and any span `s` in `d`:
>
> ```text
> d.locate(s) == Document.merge((p₁, …, pₙ)).locate(s)
> ```

Two conditions make this precise:

- **The partition must be complete and in order**, so the merged text equals the original exactly.
  Spans therefore need no remapping — the coordinate spaces coincide, and `remap` is the identity.
- **Cuts must be token-safe** — on a token boundary or in a gap between tokens. `slice` drops
  tokens a cut would truncate, so a cut through the middle of a token loses it, and the merged
  document legitimately resolves fewer boxes than the original. `tests/property/strategies.py`
  generates only token-safe partitions for this reason.

Non-adjacent merges preserve per-token geometry and page numbers, but not contiguous-text
equivalence with the source (research.md R6).

---

## Identity functions

```python
def blob_id_for(data: bytes) -> str: ...
def options_hash_for(options: Mapping[str, JsonValue]) -> str: ...
def document_id_for(
    *, blob_id: str, parser_id: str, parser_version: str, options_hash: str
) -> str: ...
```

All return `"sha256:<64 lowercase hex>"`. Derivation is fixed by ADR-0002 and
[research.md R4](../research.md).

**Guarantees**: identical bytes give identical `blob_id`; options equal in content but differing in
key order give identical `options_hash`; any change to any `document_id_for` input gives a
different result.

**Errors**: options containing `NaN`, `±Infinity`, non-string dict keys, or non-JSON types raise
`IdentityError`.

---

## Cross-cutting guarantees

| Guarantee | Scope |
|---|---|
| Immutability | No public operation mutates its receiver or arguments |
| Determinism | Identical inputs give identical outputs on every run and platform (FR-019) |
| No I/O | No filesystem, network, clock, or randomness anywhere in `kernel/` (FR-020) |
| No upward dependency | `kernel/` imports only the stdlib allowlist and `pydantic` (FR-021) |
| No silent fallback | Unavailable capability raises `CapabilityError`; never an empty stand-in (FR-022) |
| Positions in code points | All offsets are Python string indices, never bytes (FR-004) |

## Explicitly not in this contract

`fuzzy` matching · text normalization · position maps · chunking · views · parsers · persistence ·
serialization to JSON (a later, separately versioned concern).
