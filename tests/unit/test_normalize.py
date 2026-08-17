"""T019 — coordinate normalization and text assembly (research.md R6, R7).

Two guarantees are under test. Geometry is converted at the boundary, with a
tolerance that separates rendering slop from a coordinate-system bug. And
canonical text is assembled from the tokens without being normalized -- FR-007
forbids whitespace collapsing, dehyphenation, and table linearization in the
canonical IR, and the only way to know that holds is to assert it directly.
"""

from __future__ import annotations

import pytest

from docdoc.ingest.normalize import OUT_OF_PAGE_TOLERANCE, DocumentBuilder, normalize_bbox
from docdoc.kernel import (
    BBox,
    BlobRef,
    Capabilities,
    GeometryError,
    IngestProvenance,
    Span,
    blob_id_for,
    options_hash_for,
)

WIDTH, HEIGHT = 595.0, 842.0
BLOB = blob_id_for(b"fixture bytes")
SOURCE = BlobRef(blob_id=BLOB, mime_type="application/pdf", size_bytes=13)


def provenance(*, geometry: bool = True) -> IngestProvenance:
    return IngestProvenance(
        parser_id="test-parser",
        parser_version="1.0.0",
        options={},
        options_hash=options_hash_for({}),
        capabilities=Capabilities(text=True, geometry=geometry, tables=False, handwriting=False),
        text_layer_used=True,
        reading_order="test@1",
    )


class TestNormalizeBBox:
    def test_converts_to_unit_square(self) -> None:
        box = normalize_bbox(
            0.0, 0.0, WIDTH / 2, HEIGHT / 4, width=WIDTH, height=HEIGHT, page_index=0
        )

        assert box == BBox(0.0, 0.0, 0.5, 0.25)

    def test_origin_is_top_left(self) -> None:
        # y grows downward: a box near y=0 is at the top of the page.
        box = normalize_bbox(0.0, 8.0, 10.0, 20.0, width=WIDTH, height=HEIGHT, page_index=0)

        assert box.y0 < 0.05

    def test_corners_the_wrong_way_round_are_ordered_not_rejected(self) -> None:
        # Reordering corners changes notation, not the rectangle. That is a
        # different thing from reordering tokens, which is forbidden.
        box = normalize_bbox(
            WIDTH / 2, HEIGHT / 2, 0.0, 0.0, width=WIDTH, height=HEIGHT, page_index=0
        )

        assert box == BBox(0.0, 0.0, 0.5, 0.5)

    def test_slop_inside_the_tolerance_is_clamped(self) -> None:
        slop = WIDTH * OUT_OF_PAGE_TOLERANCE / 2
        box = normalize_bbox(-slop, 0.0, 10.0, 10.0, width=WIDTH, height=HEIGHT, page_index=0)

        assert box.x0 == 0.0

    def test_beyond_the_tolerance_raises_rather_than_clamping(self) -> None:
        with pytest.raises(GeometryError, match="coordinate-system error"):
            normalize_bbox(-WIDTH * 0.3, 0.0, 10.0, 10.0, width=WIDTH, height=HEIGHT, page_index=0)

    def test_a_page_with_no_area_raises(self) -> None:
        with pytest.raises(GeometryError, match="no area"):
            normalize_bbox(0.0, 0.0, 1.0, 1.0, width=0.0, height=HEIGHT, page_index=3)

    def test_error_names_the_page(self) -> None:
        with pytest.raises(GeometryError) as caught:
            normalize_bbox(
                0.0, HEIGHT * 5, 10.0, HEIGHT * 6, width=WIDTH, height=HEIGHT, page_index=7
            )

        assert caught.value.page_index == 7


class TestTextAssembly:
    def _build(self, builder: DocumentBuilder):  # type: ignore[no-untyped-def]
        return builder.build(source=SOURCE, provenance=provenance())

    def test_tokens_index_exactly_into_the_assembled_text(self) -> None:
        builder = DocumentBuilder(geometry=True)
        builder.start_page(width=WIDTH, height=HEIGHT)
        builder.add_line(
            [("Invoice", BBox(0.1, 0.1, 0.2, 0.12)), ("INV-001", BBox(0.21, 0.1, 0.3, 0.12))]
        )
        document = self._build(builder)

        for token in document.tokens:
            assert document.text[token.span.start : token.span.end] != ""
        assert document.find("INV-001") == (Span(8, 15),)

    def test_pages_tile_the_text(self) -> None:
        builder = DocumentBuilder(geometry=True)
        for _ in range(3):
            builder.start_page(width=WIDTH, height=HEIGHT)
            builder.add_line([("word", BBox(0.1, 0.1, 0.2, 0.12))])
        document = self._build(builder)

        cursor = 0
        for page in document.pages:
            assert page.span.start == cursor
            cursor = page.span.end
        assert cursor == len(document.text)

    def test_a_page_with_no_words_survives_with_zero_tokens(self) -> None:
        builder = DocumentBuilder(geometry=True)
        builder.start_page(width=WIDTH, height=HEIGHT)
        builder.add_line([("text", BBox(0.1, 0.1, 0.2, 0.12))])
        builder.start_page(width=WIDTH, height=HEIGHT)  # blank page
        document = self._build(builder)

        assert len(document.pages) == 2
        assert document.page_for(document.find("text")[0]) == (0,)


class TestNoNormalizationIsApplied:
    """FR-007 — canonical text is byte-faithful to what the parser emitted."""

    def _text_of(self, lines: list[list[str]]) -> str:
        builder = DocumentBuilder(geometry=False)
        builder.start_page(width=WIDTH, height=HEIGHT)
        for line in lines:
            builder.add_line([(word, None) for word in line])
        return builder.build(source=SOURCE, provenance=provenance(geometry=False)).text

    def test_whitespace_inside_a_word_survives(self) -> None:
        # A parser that emits "12  345" as one token keeps both spaces.
        assert "12  345" in self._text_of([["12  345"]])

    def test_hyphenated_line_breaks_are_not_rejoined(self) -> None:
        text = self._text_of([["compre-"], ["hensive"]])

        assert "compre-\nhensive" in text
        assert "comprehensive" not in text

    def test_line_structure_is_preserved_rather_than_collapsed(self) -> None:
        text = self._text_of([["a"], ["b"], ["c"]])

        assert text == "a\nb\nc\n"
