"""T049 — the performance targets from plan.md "Technical Context".

Marked ``perf`` and excluded from the default CI run: these are guardrails
against algorithmic regressions (an O(n) lookup creeping back in), not precise
benchmarks. Thresholds are deliberately loose so they fail on a complexity
mistake rather than on a slow machine.

    uv run pytest tests/perf -m perf
"""

from __future__ import annotations

import time

import pytest

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

TOKEN_COUNT = 50_000
TOKEN_WIDTH = 6  # "wordNN" plus a separator


def build_large_document() -> Document:
    words = [f"w{index:05d}" for index in range(TOKEN_COUNT)]
    text = " ".join(words)

    page_size = TOKEN_COUNT // 100
    pages: list[Page] = []
    tokens: list[Token] = []

    cursor = 0
    page_start = 0
    for index, word in enumerate(words):
        span = Span(cursor, cursor + len(word))
        page_index = index // page_size
        tokens.append(
            Token(
                span=span,
                geometry=Geometry(page_index, BBox(0.1, 0.1, 0.2, 0.13)),
                source_confidence=None,
            )
        )
        cursor += len(word) + 1
        if (index + 1) % page_size == 0 or index == len(words) - 1:
            page_end = min(cursor, len(text))
            pages.append(
                Page(index=page_index, span=Span(page_start, page_end), width=612.0, height=792.0)
            )
            page_start = page_end

    options: dict[str, object] = {}
    return Document.create(
        text=text,
        pages=tuple(pages),
        tokens=tuple(tokens),
        provenance=IngestProvenance(
            parser_id="perf",
            parser_version="1.0.0",
            options=options,
            options_hash=options_hash_for(options),
            capabilities=Capabilities(text=True, geometry=True, tables=False, handwriting=False),
            text_layer_used=True,
        ),
        source=BlobRef(blob_id=blob_id_for(b"perf"), mime_type="text/plain", size_bytes=4),
    )


@pytest.fixture(scope="module")
def large_document() -> Document:
    return build_large_document()


@pytest.mark.perf
def test_construction_of_50k_tokens_is_under_300ms() -> None:
    start = time.perf_counter()
    build_large_document()
    elapsed = time.perf_counter() - start
    assert elapsed < 0.3, f"construction took {elapsed * 1000:.0f} ms"


@pytest.mark.perf
def test_locate_p95_is_under_1ms(large_document: Document) -> None:
    """Guards the O(log n + k) lookup. A linear scan would be ~100x slower."""
    step = len(large_document.text) // 500
    timings: list[float] = []
    for offset in range(0, len(large_document.text) - TOKEN_WIDTH, step):
        span = Span(offset, offset + TOKEN_WIDTH)
        start = time.perf_counter()
        large_document.locate(span)
        timings.append(time.perf_counter() - start)

    timings.sort()
    p95 = timings[int(len(timings) * 0.95)]
    assert p95 < 0.001, f"locate p95 was {p95 * 1000:.3f} ms"


@pytest.mark.perf
def test_find_over_a_large_text_is_under_50ms(large_document: Document) -> None:
    start = time.perf_counter()
    large_document.find("w49999")
    elapsed = time.perf_counter() - start
    assert elapsed < 0.05, f"find took {elapsed * 1000:.0f} ms"


@pytest.mark.perf
def test_slice_of_50k_tokens_is_under_300ms(large_document: Document) -> None:
    """A whole-document slice costs about what construction costs.

    The result is a new Document, and a Document re-checks every invariant at
    construction -- that is the guarantee, not an overhead to optimise away. The
    original plan.md figure of 20 ms assumed slicing was cheaper than building,
    which was an unmeasured guess.
    """
    start = time.perf_counter()
    large_document.slice(Span(0, len(large_document.text)))
    elapsed = time.perf_counter() - start
    assert elapsed < 0.3, f"slice took {elapsed * 1000:.0f} ms"


@pytest.mark.perf
def test_merge_of_100_parts_is_under_300ms(large_document: Document) -> None:
    bounds = [page.span for page in large_document.pages]
    parts = [large_document.slice(span) for span in bounds]

    start = time.perf_counter()
    Document.merge(parts)
    elapsed = time.perf_counter() - start
    assert elapsed < 0.3, f"merge of {len(parts)} parts took {elapsed * 1000:.0f} ms"
