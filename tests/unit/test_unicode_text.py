"""T066, T070 — Unicode text and the degenerate document, per spec Edge Cases.

The spec lists ligatures, combining marks, characters outside the BMP, and
right-to-left script as cases where "positions and geometry stay consistent".
Assembling canonical text from tokens is exactly where that could quietly break,
so it is worth asserting rather than assuming.

**Coverage is split, deliberately.** No font in this repository carries combining
marks, ligatures, astral characters, or Arabic — they come back from a PDF as
replacement glyphs or vanish entirely, so a PDF fixture containing them would
pass while asserting nothing. Those cases are therefore exercised at the builder
level, where no font is involved and the offsets are the whole point. What the
PDF fixture does cover is real end-to-end multi-byte text: precomposed Latin
accents and CJK, both verified to round-trip.
"""

from __future__ import annotations

import unicodedata
from pathlib import Path

import pytest

from docdoc.ingest import parse
from docdoc.ingest.errors import UnsupportedDocumentError
from docdoc.ingest.normalize import DocumentBuilder
from docdoc.kernel import (
    BlobRef,
    Capabilities,
    IngestProvenance,
    blob_id_for,
    options_hash_for,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"
BLOB = blob_id_for(b"unicode")
SOURCE = BlobRef(blob_id=BLOB, mime_type="application/pdf", size_bytes=7)


def text_of(lines: list[list[str]]) -> object:
    """Build a document straight from words, with no font in the way."""
    builder = DocumentBuilder(geometry=False)
    builder.start_page(width=595.0, height=842.0)
    for line in lines:
        builder.add_line([(word, None) for word in line])
    return builder.build(
        source=SOURCE,
        provenance=IngestProvenance(
            parser_id="test",
            parser_version="1.0.0",
            options={},
            options_hash=options_hash_for({}),
            capabilities=Capabilities(text=True, geometry=False, tables=False, handwriting=False),
            text_layer_used=True,
            reading_order="test@1",
        ),
    )


class TestThroughARealPdf:
    """End-to-end, with the multi-byte text the toolchain can actually embed."""

    @pytest.fixture
    def document(self) -> object:
        """SC-013 — the base install has no native PDF reader, and this needs one.

        Guarded in the fixture rather than on the class, because every test here
        takes ``document`` and the fixture is the single place the requirement
        actually bites. A class decorator would have to be remembered by whoever
        adds the next test; this cannot be forgotten.
        """
        pytest.importorskip("pymupdf")
        return parse((FIXTURES / "pdf" / "unicode_text.pdf").read_bytes())

    def test_nothing_was_lost_to_a_broken_decode(self, document) -> None:  # type: ignore[no-untyped-def]
        assert "�" not in document.text
        assert document.provenance.text_layer.pages[0].char_count > 100

    @pytest.mark.parametrize(
        "needle",
        ["numéro", "Société", "l'Église", "Château-Thébaud", "à payer", "发票", "金额合计"],
    )
    def test_a_multi_byte_value_resolves_to_a_page_and_a_box(
        self,
        document,  # type: ignore[no-untyped-def]
        needle: str,
    ) -> None:
        (span,) = document.find(needle)

        assert document.text[span.start : span.end] == needle
        # A multi-word needle covers more than one token, so one geometry per
        # covering token is the correct answer, not a single box.
        boxes = document.locate(span)
        assert boxes
        for geometry in boxes:
            assert geometry.page_index == 0
            assert all(0.0 <= value <= 1.0 for value in geometry.bbox)

    def test_every_token_slice_is_exactly_its_word(self, document) -> None:  # type: ignore[no-untyped-def]
        for token in document.tokens:
            assert document.text[token.span.start : token.span.end].strip()

    def test_offsets_are_code_points_not_bytes(self, document) -> None:  # type: ignore[no-untyped-def]
        # "发票" is six bytes in UTF-8 and two code points. A byte-based offset
        # would put every span after it in the wrong place.
        (span,) = document.find("发票")

        assert span.end - span.start == 2
        assert len(document.text.encode()) > len(document.text)


class TestAtTheBuilderLevel:
    """The cases no available font can embed (see the module docstring)."""

    def test_combining_marks_keep_their_own_code_points(self) -> None:
        decomposed = "café"  # e + COMBINING ACUTE ACCENT
        document = text_of([[decomposed]])

        (span,) = document.find(decomposed)  # type: ignore[attr-defined]
        assert span.end - span.start == 5
        assert "café" not in document.text  # type: ignore[attr-defined]

    def test_precomposed_and_decomposed_forms_stay_distinct(self) -> None:
        """FR-007 — no Unicode normalization is applied to canonical text.

        If the layer normalized, these two would collapse into one and a span
        from one form would silently match the other.
        """
        document = text_of([["café", "café"]])

        assert unicodedata.normalize("NFC", "café") == "café"
        assert len(document.find("café")) == 1  # type: ignore[attr-defined]
        assert len(document.find("café")) == 1  # type: ignore[attr-defined]

    def test_ligatures_are_not_expanded(self) -> None:
        document = text_of([["ﬁnal"]])  # LATIN SMALL LIGATURE FI

        assert document.find("ﬁnal")  # type: ignore[attr-defined]
        assert not document.find("final")  # type: ignore[attr-defined]

    def test_characters_outside_the_bmp_count_as_one_position_each(self) -> None:
        # Python strings are code points, so an astral character is one position.
        # A UTF-16 implementation would make it two and misplace everything after.
        astral = "\U0001d7cf\U0001d7d0\U0001d7d1"  # MATHEMATICAL BOLD DIGIT ONE..THREE
        document = text_of([["Total", astral]])

        (span,) = document.find(astral)  # type: ignore[attr-defined]
        assert span.end - span.start == 3

    def test_right_to_left_script_keeps_logical_order(self) -> None:
        # Spans index logical order, which is what a caller searches in. Visual
        # order is a rendering concern and none of the kernel's business.
        arabic = "العربية"
        document = text_of([["Language:", arabic]])

        (span,) = document.find(arabic)  # type: ignore[attr-defined]
        assert document.text[span.start : span.end] == arabic  # type: ignore[attr-defined]

    def test_mixed_direction_text_stays_addressable(self) -> None:
        document = text_of([["Total", "العربية", "228.00"]])

        for needle in ("Total", "العربية", "228.00"):
            (span,) = document.find(needle)  # type: ignore[attr-defined]
            assert document.text[span.start : span.end] == needle  # type: ignore[attr-defined]

    def test_a_zero_width_joiner_is_preserved(self) -> None:
        # Emoji sequences and Indic scripts depend on it; stripping it would
        # change the text.
        joined = "ক্‍য"
        document = text_of([[joined]])

        assert "‍" in document.text  # type: ignore[attr-defined]
        assert document.find(joined)  # type: ignore[attr-defined]


class TestZeroPageDocument:
    """T070 — the degenerate PDF, per spec Edge Cases.

    The fixture is hand-written: PyMuPDF refuses to save a zero-page document
    ("cannot save with zero pages"), so the only way to have one is to author the
    bytes.
    """

    def test_it_is_refused_as_unreadable_rather_than_routed(self) -> None:
        # Left to the text-layer rule this would come out "not usable" and the
        # caller would be told to find a recognition parser for a document with
        # nothing to recognize. Saying what is actually wrong is more use.
        with pytest.raises(UnsupportedDocumentError) as caught:
            parse((FIXTURES / "pdf" / "zero_pages.pdf").read_bytes())

        assert caught.value.reason == "corrupt"
        assert "no pages" in str(caught.value)

    def test_the_refusal_names_the_file_and_no_content(self) -> None:
        with pytest.raises(UnsupportedDocumentError) as caught:
            parse((FIXTURES / "pdf" / "zero_pages.pdf").read_bytes())

        assert caught.value.blob_id is not None
        assert caught.value.media_type == "application/pdf"

    def test_it_is_still_recognized_as_a_pdf(self) -> None:
        # The bytes are a valid PDF; what it lacks is content. Those are
        # different failures and must not be conflated.
        from docdoc.ingest.source import SourceFile

        source = SourceFile.from_bytes((FIXTURES / "pdf" / "zero_pages.pdf").read_bytes())

        assert source.media_type == "application/pdf"
