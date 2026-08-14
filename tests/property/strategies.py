"""T032 — Hypothesis strategies generating valid documents.

Generated documents must satisfy every construction invariant (DOC-1..DOC-10),
otherwise the property tests would spend their budget rediscovering that
``Document`` rejects malformed input, which the unit tests already cover.

The alphabet deliberately includes Vietnamese diacritics, combining marks, and
characters outside the Basic Multilingual Plane: code-point positions are a
stated requirement (FR-004), so they belong in the generator rather than in a
handful of hand-written cases.
"""

from __future__ import annotations

from itertools import pairwise

from hypothesis import strategies as st

from docdoc.kernel import (
    BBox,
    BlobRef,
    Capabilities,
    Document,
    Geometry,
    IngestProvenance,
    Page,
    Span,
    Token,
    blob_id_for,
    options_hash_for,
)

TEXT_ALPHABET = st.sampled_from(
    [
        "a",
        "b",
        "c",
        "x",
        "y",
        "z",
        "0",
        "1",
        "2",
        " ",
        "\n",
        "-",
        ":",
        ".",
        "ô",
        "ế",
        "ộ",
        "ư",  # Vietnamese diacritics
        "é",  # combining acute
        "🧾",  # outside the BMP
        "­",  # soft hyphen
    ]
)

document_text = st.lists(TEXT_ALPHABET, min_size=0, max_size=60).map("".join)


def _bbox_for(sequence: int) -> BBox:
    """Deterministic, well-spread geometry.

    Values are rounded so equality comparisons across a slice/merge round trip
    are exact rather than approximate.
    """
    slot = (sequence % 10) / 10.0
    return BBox(round(slot, 4), 0.1, round(slot + 0.09, 4), 0.14)


@st.composite
def documents(
    draw: st.DrawFn,
    *,
    with_geometry: bool | None = None,
    max_pages: int = 4,
) -> Document:
    """A valid Document with random text, pages, and token layout."""
    text = draw(document_text)
    geometry_enabled = draw(st.booleans()) if with_geometry is None else with_geometry

    # There are only len(text) + 1 distinct cut positions, so a document cannot
    # have more pages than that. Short and empty texts get a single page.
    page_count = draw(st.integers(min_value=1, max_value=min(max_pages, len(text) + 1)))
    cut_points = sorted(
        draw(
            st.lists(
                st.integers(min_value=0, max_value=len(text)),
                min_size=page_count - 1,
                max_size=page_count - 1,
                unique=True,
            )
        )
    )
    bounds = [0, *cut_points, len(text)]
    pages = tuple(
        Page(index=index, span=Span(start, end), width=612.0, height=792.0)
        for index, (start, end) in enumerate(pairwise(bounds))
    )

    tokens: list[Token] = []
    sequence = 0
    for page in pages:
        cursor = page.span.start
        while cursor < page.span.end:
            gap = draw(st.integers(min_value=0, max_value=2))
            cursor += gap
            if cursor >= page.span.end:
                break
            remaining = page.span.end - cursor
            length = draw(st.integers(min_value=1, max_value=min(5, remaining)))
            geometry = Geometry(page.index, _bbox_for(sequence)) if geometry_enabled else None
            tokens.append(Token(span=Span(cursor, cursor + length), geometry=geometry))
            cursor += length
            sequence += 1

    data = f"synthetic:{text}".encode()
    provenance = IngestProvenance(
        parser_id="property_parser",
        parser_version="1.0.0",
        options={},
        options_hash=options_hash_for({}),
        capabilities=Capabilities(
            text=True, geometry=geometry_enabled, tables=False, handwriting=False
        ),
        text_layer_used=True,
    )
    source = BlobRef(blob_id=blob_id_for(data), mime_type="application/pdf", size_bytes=len(data))

    return Document.create(
        text=text, pages=pages, tokens=tokens, provenance=provenance, source=source
    )


def token_safe_cut_points(document: Document) -> list[int]:
    """Positions where the document can be cut without truncating a token.

    ``slice`` drops tokens a cut would truncate, because keeping a clipped token
    would leave its geometry describing glyphs that are no longer present. The
    round-trip invariant therefore holds for partitions whose cuts fall on token
    boundaries or in the gaps between tokens -- which is what this returns.
    """
    interior = {
        position
        for token in document.tokens
        for position in range(token.span.start + 1, token.span.end)
    }
    return [position for position in range(len(document.text) + 1) if position not in interior]


@st.composite
def documents_with_partition(
    draw: st.DrawFn, *, with_geometry: bool | None = None
) -> tuple[Document, tuple[Document, ...]]:
    """A document together with a token-safe partition of it."""
    document = draw(documents(with_geometry=with_geometry))
    safe = token_safe_cut_points(document)
    chosen = sorted(
        draw(st.lists(st.sampled_from(safe), min_size=0, max_size=4, unique=True)) if safe else []
    )
    bounds = [0, *[c for c in chosen if 0 < c < len(document.text)], len(document.text)]
    parts = tuple(document.slice(Span(start, end)) for start, end in pairwise(bounds))
    return document, parts


@st.composite
def spans_within(draw: st.DrawFn, document: Document) -> Span:
    """An arbitrary valid span over the document's text."""
    limit = len(document.text)
    start = draw(st.integers(min_value=0, max_value=limit))
    end = draw(st.integers(min_value=start, max_value=limit))
    return Span(start, end)
