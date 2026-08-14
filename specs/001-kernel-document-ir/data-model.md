# Phase 1 Data Model: Kernel and Canonical Document IR

**Feature**: `001-kernel-document-ir` | **Date**: 2026-08-14

Entity definitions, invariants, and validation rules. Type strategy follows
[research.md R1](research.md): hot primitives are `NamedTuple`, aggregates are frozen Pydantic
models. Field types are shown as Python annotations because they *are* the contract for a library.

---

## Value primitives (`kernel/span.py`, `kernel/geometry.py`, `kernel/token.py`)

### `Span`

```python
class Span(NamedTuple):
    start: int
    end: int
```

Half-open `[start, end)` range of positions in `Document.text`, counted in Unicode code points.

| Invariant | Rule | Enforced |
|---|---|---|
| SP-1 | `0 <= start <= end` | On construction via `Span.create()`; raises `SpanError` |
| SP-2 | `end <= len(document.text)` when used against a document | At use site; raises `SpanError` |
| SP-3 | `len(span) == end - start`; a zero-length span is valid | By definition |

Derived: `is_empty`, `__len__`, `contains(pos)`, `intersects(other)`, `shift(delta)`.
`shift` is the rebasing primitive `merge()` and `slice()` are built from.

> The bare `NamedTuple` constructor cannot reject bad input, so SP-1 is enforced by the
> `Span.create()` classmethod and by every kernel operation that accepts a span. Direct
> `Span(5, 2)` construction is possible in Python and is treated as caller error; all kernel
> entry points validate before use.

### `BBox`

```python
class BBox(NamedTuple):
    x0: float
    y0: float
    x1: float
    y1: float
```

| Invariant | Rule |
|---|---|
| BB-1 | `0.0 <= x0 <= x1 <= 1.0` and `0.0 <= y0 <= y1 <= 1.0` |
| BB-2 | Origin is top-left; `y` increases downward |
| BB-3 | Coordinates are normalized to the page; no absolute units appear in the kernel |
| BB-4 | Zero-area boxes are valid (a zero-width token) |

Derived: `width`, `height`, `area`, `union(other)`, `intersects(other)`.
Non-finite coordinates raise `GeometryError`.

### `Geometry`

```python
class Geometry(NamedTuple):
    page_index: int
    bbox: BBox
```

A box anchored to one page. `page_index` is 0-based and must reference an existing page (GE-1).
A `BBox` alone is never meaningful — geometry is always page-anchored.

### `Token`

```python
class Token(NamedTuple):
    span: Span
    geometry: Geometry | None
    source_confidence: float | None
```

The smallest addressable unit. **Token carries no `text` field** — its text is
`document.text[token.span.start:token.span.end]`. See research.md "Deviations".

| Invariant | Rule |
|---|---|
| TK-1 | `geometry is None` only when the producing parser lacks the geometry capability |
| TK-2 | `source_confidence`, when present, is in `[0.0, 1.0]` and is stored verbatim, never interpreted |
| TK-3 | The token's span lies within the page's span (collection-level, checked at document construction) |

---

## Aggregates (frozen Pydantic models)

### `Page` (`kernel/page.py`)

```python
class Page(BaseModel, frozen=True):
    index: int  # 0-based, contiguous from 0
    span: Span  # range of Document.text belonging to this page
    width: float  # source units (points/pixels), for reference only
    height: float
    rotation: int = 0  # degrees: 0, 90, 180, or 270
```

| Invariant | Rule |
|---|---|
| PG-1 | `width > 0` and `height > 0` |
| PG-2 | `rotation in {0, 90, 180, 270}` |
| PG-3 | Page spans are ordered, non-overlapping, and cover `Document.text` exactly (collection-level) |

`width`/`height` are retained for provenance and for adapters converting native coordinates. The
kernel never computes with them — geometry arrives already normalized.

### `Block` (`kernel/block.py`)

```python
class BlockKind(StrEnum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE = "figure"
    HEADER = "header"
    FOOTER = "footer"
    CAPTION = "caption"
    OTHER = "other"


class Block(BaseModel, frozen=True):
    span: Span
    kind: BlockKind
    page_index: int
    geometry: Geometry | None = None
```

Blocks may nest logically but are stored flat. Unlike tokens, **blocks may overlap** (a table block
containing paragraph blocks), so they are not part of the `SpanIndex`.

### `Table` and `TableCell` (`kernel/table.py`)

```python
class TableCell(BaseModel, frozen=True):
    span: Span
    row: int
    column: int
    row_span: int = 1
    column_span: int = 1
    geometry: Geometry | None = None


class Table(BaseModel, frozen=True):
    span: Span
    page_index: int
    n_rows: int
    n_columns: int
    cells: tuple[TableCell, ...]
    geometry: Geometry | None = None
```

| Invariant | Rule |
|---|---|
| TB-1 | `0 <= row < n_rows`, `0 <= column < n_columns` for every cell |
| TB-2 | `row_span >= 1`, `column_span >= 1` |
| TB-3 | No two cells occupy the same grid position once spans are expanded |
| TB-4 | Tables are optional; absence is normal, not an error |

### `BlobRef` (`kernel/blob.py`)

```python
class BlobRef(BaseModel, frozen=True):
    blob_id: str  # "sha256:<64 lowercase hex>"
    mime_type: str
    size_bytes: int
    filename: str | None = None
```

Reference to the original file. **Never carries bytes** (FR-003). `blob_id` format is validated by
pattern; `size_bytes >= 0`.

### `Capabilities` (`kernel/provenance.py`)

```python
class Capabilities(BaseModel, frozen=True):
    text: bool
    geometry: bool
    tables: bool
    handwriting: bool
```

Declares what the producing parser supplied. Drives `CapabilityError`: requesting geometry from a
document whose `capabilities.geometry is False` raises rather than returning empty (FR-022).

### `IngestProvenance` (`kernel/provenance.py`)

```python
class IngestProvenance(BaseModel, frozen=True):
    parser_id: str
    parser_version: str
    options: Mapping[str, JsonValue]
    options_hash: str  # "sha256:<hex>" over canonical JSON
    capabilities: Capabilities
    text_layer_used: bool  # native text layer vs. recognition (Principle V)
```

No timestamp field: the kernel cannot read the clock (FR-020). Processing time is recorded by the
pipeline layer, which may.

### `Document` (`kernel/document.py`)

```python
class Document(BaseModel, frozen=True):
    id: str  # "sha256:<hex>", per ADR-0002
    text: str
    pages: tuple[Page, ...]
    tokens: SpanIndex  # ordered, non-overlapping
    blocks: tuple[Block, ...] = ()
    tables: tuple[Table, ...] = ()
    provenance: IngestProvenance
    source: BlobRef
```

**Construction-time invariants** (FR-007, FR-024 — all checked in one validator; a document that
violates any of them cannot exist):

| ID | Rule | Error |
|---|---|---|
| DOC-1 | Every token span satisfies `0 <= start <= end <= len(text)` | `DocumentInvariantError` |
| DOC-2 | Token spans are strictly ascending by `start` | `DocumentInvariantError` |
| DOC-3 | Token spans do not overlap (`t[i].end <= t[i+1].start`) | `DocumentInvariantError` |
| DOC-4 | Page indices are contiguous from 0 | `DocumentInvariantError` |
| DOC-5 | Page spans are ordered, non-overlapping, and cover `[0, len(text))` exactly | `DocumentInvariantError` |
| DOC-6 | Every `page_index` on a token, block, or table references an existing page | `DocumentInvariantError` |
| DOC-7 | Block and table spans lie within `text` | `DocumentInvariantError` |
| DOC-8 | If `capabilities.geometry` is True, every token has geometry; if False, none do | `DocumentInvariantError` |
| DOC-9 | `id` matches the ADR-0002 derivation from `source.blob_id` and `provenance` | `IdentityError` |

DOC-8 is deliberately all-or-nothing. Partial geometry would make `locate()` silently lossy — some
tokens resolving and others not, with no way for a caller to tell whether an empty result means
"not found" or "not available". A parser with partial geometry must declare
`capabilities.geometry = False` and supply none.

> **Gap flagged for `/speckit-tasks`**: the spec's edge-case list admits "geometry for only some
> tokens" as a case to handle. DOC-8 resolves it by rejection rather than partial support. This is
> the stricter reading of FR-022 and is recorded here as the decision.

### `SpanIndex` (`kernel/span_index.py`)

```python
class SpanIndex:
    _tokens: tuple[Token, ...]
    _starts: tuple[int, ...]  # parallel array for bisect
```

Immutable, iterable, `len()`-able. Built once at document construction.

| Operation | Complexity |
|---|---|
| `tokens_in(span)` | O(log n + k) |
| `token_at(pos)` | O(log n) |
| `__iter__`, `__len__` | O(n), O(1) |

---

## Identity derivation (`kernel/identity.py`)

```python
blob_id = "sha256:" + sha256(original_bytes).hexdigest()
options_hash = "sha256:" + sha256(canonical_json(options)).hexdigest()
document_id = (
    "sha256:"
    + sha256(
        canonical_json(
            {
                "v": 1,
                "blob_id": ...,
                "parser_id": ...,
                "parser_version": ...,
                "options_hash": ...,
            }
        )
    ).hexdigest()
)
```

`canonical_json` is defined in research.md R3. Rejects non-finite floats and non-string dict keys
with `IdentityError`.

---

## Error hierarchy (`kernel/errors.py`)

```text
DocdocError
└── KernelError
    ├── SpanError                 span: Span, text_length: int
    ├── GeometryError             bbox: BBox | None, page_index: int | None
    ├── DocumentInvariantError    rule: str, detail: str
    ├── MergeError                reason: str, part_ids: tuple[str, ...]
    ├── CapabilityError           capability: str, available: bool, parser_id: str
    └── IdentityError             field: str, detail: str
```

Every error carries structured attributes, not just a message (FR-023), so higher layers can
translate without parsing strings.

---

## Entity relationships

```text
BlobRef ──1:N── Document          (one file, many parses — ADR-0002)
Document ──1:1── IngestProvenance
Document ──1:N── Page             (contiguous, covering, non-overlapping)
Document ──1:1── SpanIndex ──1:N── Token   (ordered, non-overlapping)
Document ──1:N── Block            (flat, may overlap)
Document ──1:N── Table ──1:N── TableCell
Token ──0:1── Geometry ──1:1── BBox
Geometry ──N:1── Page             (via page_index)
```

## State transitions

None. Every entity is immutable and has no lifecycle. `slice()` and `merge()` produce new
`Document` values; they do not transition an existing one.
