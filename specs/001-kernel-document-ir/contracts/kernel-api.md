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
- Pages intersecting `span` are retained with spans clipped and rebased; `index` values are
  renumbered contiguously from 0, and every `page_index` reference is remapped.
- Blocks and tables fully contained in `span` are retained and rebased; partial ones are dropped.
- `result.source` and `result.provenance` are carried over unchanged.
- `result.id` is **recomputed**; a slice is a distinct document with a distinct identity.
- An empty span yields an empty document that still carries `source` and `provenance` (US2-5).

**Errors**: out-of-range span raises `SpanError`.

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

- All parts share the same `source.blob_id`, `provenance.parser_id`, and
  `provenance.parser_version` (FR-013).
- Parts' original ranges do not overlap ([research.md R6](../research.md)).

**Postconditions**:

- `result.text` is the parts' texts concatenated in the given order, with no separator.
- Token spans are shifted by the running offset of their part; **geometry is unchanged**, so every
  token still resolves to its true original page and box.
- Pages are concatenated and renumbered contiguously; duplicate pages appearing in more than one
  part are coalesced.
- `merge(())` returns an empty document — an error, since there is no `source` or `provenance` to
  carry: raises `MergeError`.
- `merge((d,))` returns a document equal to `d` in text, tokens, and geometry.
- `result.id` is recomputed.

**Errors**:

| Condition | Error |
|---|---|
| Parts differ in `blob_id`, `parser_id`, or `parser_version` | `MergeError(reason="mismatched_source")` |
| Parts' original ranges overlap | `MergeError(reason="overlapping_parts")` |
| `parts` is empty | `MergeError(reason="no_parts")` |

---

## The round-trip invariant

The property all other guarantees rest on (FR-012, SC-002):

> For any document `d` partitioned into disjoint, ordered slices `p₁…pₙ`, and any span `s` in `d`:
>
> ```text
> d.locate(s) == Document.merge((p₁, …, pₙ)).locate(remap(s))
> ```
>
> where `remap` carries `s` through the partition into the merged document's coordinate space.

Guaranteed for **partitions** — disjoint slices in original order. Non-adjacent merges preserve
per-token geometry but not contiguous-text equivalence (research.md R6).

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
