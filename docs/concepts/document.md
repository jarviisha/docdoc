# The Document IR

The canonical Document Intermediate Representation is the most important artifact in docdoc.
Everything else — grounding, evaluation, human review, audit — degrades to guesswork the moment
source location is discarded, and discarded provenance cannot be recovered later.

## A document is never a string

The one thing docdoc will not do is reduce a document to `str`. A `Document` preserves:

| Part | What it holds |
|---|---|
| `text` | canonical text, byte-faithful to what the parser emitted |
| `pages` | physical pages, tiling the text exactly |
| `tokens` | the smallest addressable units, with geometry |
| `blocks` | structural regions (paragraph, heading, table…) |
| `tables` | rows and cells, when the parser supplies them |
| `provenance` | which parser, which version, which options, text layer or not |
| `source` | a reference to the original file — never its bytes |
| `id` | identity of *this parse* (see [identity](identity.md)) |
| `origin` | which ranges of the original parse this document occupies |

`Document` is immutable. Operations that change it return a new value.

## Positions are code points

All offsets are Python string indices — **Unicode code points**, not bytes and not grapheme
clusters. So `text[span.start:span.end]` is correct by construction.

This matters immediately rather than theoretically: byte offsets would break on the first
Vietnamese invoice. `"Công ty"` is 7 code points and 9 bytes.

```python
Span(0, 4)  # "Công"
len(Span(0, 4))  # 4 — character length, not field count
```

Spans are **half-open**, `[start, end)`. An empty span is valid, and it intersects nothing —
which is what lets `locate()` return an empty result for a zero-length query rather than
inventing one.

## Tokens carry no text

A `Token` holds a span, geometry, and the parser's own confidence — but **not** its text. Its text
is `document.text[token.span.start:token.span.end]`.

Storing text on the token would duplicate the entire document and, worse, create a
duplicate-state invariant (`token.text == text[span]`) that could drift out of sync. Deriving it
removes that failure mode outright.

## Geometry is normalized and page-anchored

Boxes are normalized to `0.0..1.0` with a **top-left origin**, so `y` increases downward.
Provider-native coordinate systems are converted in the adapter; absolute units never reach the
kernel.

A `BBox` alone is meaningless — the same coordinates describe different physical locations on
different pages. Geometry is therefore always a `(page_index, bbox)` pair.

## Geometry is all-or-nothing

Within one document, either every token has geometry or none does. Partial geometry is rejected at
construction (DOC-8).

This looks strict, and it is deliberate. If geometry were partial, `locate()` returning an empty
result would be ambiguous: did no token cover that range, or did that token simply lack a box? A
caller could not tell, and would confidently draw the wrong conclusion. A parser with partial
geometry must declare `capabilities.geometry = False` and supply none — then `locate()` raises
`CapabilityError`, which is unambiguous.

## An invalid document cannot exist

Ten invariants are checked once, at construction. Nothing downstream has to defend against a
malformed document.

| Rule | What it guarantees |
|---|---|
| DOC-1 | token spans fit the text |
| DOC-2 | tokens are ordered by ascending start |
| DOC-3 | tokens never overlap |
| DOC-4 | page indices are strictly ascending and unique |
| DOC-5 | page spans tile the text exactly |
| DOC-6 | every page reference resolves, and tokens sit on their own page |
| DOC-7 | block and table spans fit the text |
| DOC-8 | geometry is present for every token or for none |
| DOC-9 | `id` matches its derivation |
| DOC-10 | origin ranges are ordered, disjoint, and account for all the text |

DOC-2 and DOC-3 are what let `SpanIndex` use a binary search: with non-overlapping ordered tokens,
interval stabbing reduces to `bisect`. An interval tree would only earn its complexity if
intervals could overlap, which construction forbids.

## The four operations

```python
document.locate(span)  # -> (Geometry, ...)  one per intersecting token
document.page_for(span)  # -> (int, ...)       works without geometry
document.find(text)  # -> (Span, ...)      exact, non-overlapping
document.slice(span)  # -> Document
Document.merge(parts)  # -> Document
```

**`locate` does no interpolation and no grouping.** A range covering half a token returns that
token's whole box, because parsers report geometry per token and interpolating a partial box would
assume uniform glyph advance — false for proportional fonts and for every complex script. Nor does
it merge boxes into per-line rectangles: that needs a tunable heuristic, and a union across a
multi-line span would produce one large rectangle covering unrelated text. Both would be
confidently wrong in exactly the audit context docdoc exists to serve.

**`slice` drops tokens it would truncate.** A clipped token's geometry would describe glyphs no
longer present in the sliced text. Losing a token is recoverable; a wrong box is not.

**`slice` preserves page numbers.** A slice of page 7 still reports page 7. This is why page
indices are only required to be ascending, not contiguous from zero.

## The invariant everything rests on

```text
locate(span) == merge(partition(document)).locate(span)
```

Cutting a document into disjoint parts and reassembling them must not change where anything came
from. The property suite verifies this across thousands of generated documents.

It holds for partitions whose cuts fall on token boundaries or in the gaps between tokens — which
is the honest statement, given that a cut through a token drops it by design.
