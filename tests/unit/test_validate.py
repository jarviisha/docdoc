"""T022 — holding a parser to what it declared (ING-4, ING-8).

The failures here must name the parser. When the parser is a remote service you
cannot read the output of, "token 41 overlaps token 40, from parser azure-di" is
the difference between a bug report and a shrug.
"""

from __future__ import annotations

import pytest

from docdoc.ingest.capabilities import ParserCapabilities
from docdoc.ingest.errors import ParserError
from docdoc.ingest.validate import check_token_order, validate_output
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

BLOB = blob_id_for(b"bytes")
SOURCE = BlobRef(blob_id=BLOB, mime_type="application/pdf", size_bytes=5)
CAPS_WITH_GEOMETRY = ParserCapabilities(
    text=True, geometry=True, media_types=frozenset({"application/pdf"})
)
CAPS_TEXT_ONLY = ParserCapabilities(
    text=True, geometry=False, media_types=frozenset({"application/pdf"})
)


def make_document(*, geometry: bool) -> Document:
    text = "alpha beta\n"
    boxes = [BBox(0.1, 0.1, 0.2, 0.12), BBox(0.25, 0.1, 0.35, 0.12)]
    tokens = [
        Token(
            span=Span(0, 5),
            geometry=Geometry(page_index=0, bbox=boxes[0]) if geometry else None,
        ),
        Token(
            span=Span(6, 10),
            geometry=Geometry(page_index=0, bbox=boxes[1]) if geometry else None,
        ),
    ]
    return Document.create(
        text=text,
        pages=[Page(index=0, span=Span(0, len(text)), width=595.0, height=842.0)],
        tokens=tokens,
        provenance=IngestProvenance(
            parser_id="stub",
            parser_version="1.0.0",
            options={},
            options_hash=options_hash_for({}),
            capabilities=Capabilities(
                text=True, geometry=geometry, tables=False, handwriting=False
            ),
            text_layer_used=True,
        ),
        source=SOURCE,
    )


class TestTokenOrder:
    def test_ascending_non_overlapping_passes(self) -> None:
        check_token_order(
            [Token(span=Span(0, 5)), Token(span=Span(6, 10))], parser_id="stub", blob_id=BLOB
        )

    def test_adjacent_tokens_are_fine(self) -> None:
        # end == next start is touching, not overlapping.
        check_token_order(
            [Token(span=Span(0, 5)), Token(span=Span(5, 9))], parser_id="stub", blob_id=BLOB
        )

    def test_overlapping_is_rejected(self) -> None:
        with pytest.raises(ParserError) as caught:
            check_token_order(
                [Token(span=Span(0, 6)), Token(span=Span(4, 10))],
                parser_id="stub",
                blob_id=BLOB,
            )

        assert caught.value.reason == "invalid_order"
        assert caught.value.parser_id == "stub"

    def test_out_of_order_is_rejected(self) -> None:
        with pytest.raises(ParserError) as caught:
            check_token_order(
                [Token(span=Span(6, 10)), Token(span=Span(0, 5))],
                parser_id="stub",
                blob_id=BLOB,
            )

        assert caught.value.reason == "invalid_order"

    def test_nothing_is_silently_sorted(self) -> None:
        # The whole point: bad order raises rather than being repaired into a
        # plausible-looking result (FR-037).
        tokens = [Token(span=Span(6, 10)), Token(span=Span(0, 5))]

        with pytest.raises(ParserError):
            check_token_order(tokens, parser_id="stub", blob_id=BLOB)

        assert tokens[0].span.start == 6  # untouched


class TestCapabilityHonesty:
    def test_declaring_geometry_and_supplying_it_passes(self) -> None:
        document = make_document(geometry=True)

        assert validate_output(document, CAPS_WITH_GEOMETRY, parser_id="stub") is document

    def test_declaring_no_geometry_and_supplying_none_passes(self) -> None:
        document = make_document(geometry=False)

        assert validate_output(document, CAPS_TEXT_ONLY, parser_id="stub") is document

    def test_declaring_geometry_but_supplying_none_is_rejected(self) -> None:
        document = make_document(geometry=False)

        with pytest.raises(ParserError) as caught:
            validate_output(document, CAPS_WITH_GEOMETRY, parser_id="stub", blob_id=BLOB)

        assert caught.value.reason == "capability_mismatch"

    def test_under_declaring_is_rejected_too(self) -> None:
        # A caller told there is no geometry will never look for the geometry
        # that is in fact there.
        document = make_document(geometry=True)

        with pytest.raises(ParserError) as caught:
            validate_output(document, CAPS_TEXT_ONLY, parser_id="stub", blob_id=BLOB)

        assert caught.value.reason == "capability_mismatch"

    def test_errors_carry_the_blob_id_and_no_content(self) -> None:
        document = make_document(geometry=True)

        with pytest.raises(ParserError) as caught:
            validate_output(document, CAPS_TEXT_ONLY, parser_id="stub", blob_id=BLOB)

        assert caught.value.blob_id == BLOB
        assert "alpha" not in str(caught.value)
