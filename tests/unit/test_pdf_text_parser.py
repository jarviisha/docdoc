"""T028, T030, T031 — the native PDF path, including the two library
behaviours the plan assumed and this file settles.

T030 (reading order) and T031 (rotation) were written into the plan as
assumption-resolution tasks: each could change what the adapter declares. Both
turned out to need the fallback, and the tests below are what pinned them.
"""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytest

from docdoc.ingest.errors import UnsupportedDocumentError
from docdoc.ingest.options import TransportSettings
from docdoc.ingest.parsers.pdf_text import PdfTextParser
from docdoc.ingest.source import SourceFile

FIXTURES = Path(__file__).parent.parent / "fixtures" / "pdf"


def source_for(name: str) -> SourceFile:
    path = FIXTURES / name
    return SourceFile.from_bytes(path.read_bytes(), filename=path.name)


def parse(name: str):  # type: ignore[no-untyped-def]
    return PdfTextParser().parse(source_for(name), {}, TransportSettings())


class TestDigitalPdf:
    def test_text_and_pages(self) -> None:
        document = parse("digital_invoice.pdf")

        assert len(document.pages) == 1
        assert "INV-001" in document.text

    def test_a_value_resolves_to_a_page_and_a_box(self) -> None:
        document = parse("digital_invoice.pdf")

        (span,) = document.find("INV-001")
        (geometry,) = document.locate(span)

        assert geometry.page_index == 0
        assert 0.0 <= geometry.bbox.x0 < geometry.bbox.x1 <= 1.0
        assert 0.0 <= geometry.bbox.y0 < geometry.bbox.y1 <= 1.0

    def test_page_dimensions_come_from_the_file(self) -> None:
        document = parse("digital_invoice.pdf")

        assert document.pages[0].width == pytest.approx(595.0, abs=1.0)
        assert document.pages[0].height == pytest.approx(842.0, abs=1.0)

    def test_every_token_carries_geometry(self) -> None:
        document = parse("digital_invoice.pdf")

        assert document.tokens
        assert all(token.geometry is not None for token in document.tokens)


class TestBlankAndMixedPages:
    def test_a_page_with_no_text_layer_survives_with_zero_tokens(self) -> None:
        # mixed_pages.pdf is two digital pages and one scanned page. Routing is
        # whole-document, so the scanned page contributes nothing -- and must
        # still be present, with its dimensions (FR-035).
        document = parse("mixed_pages.pdf")

        assert len(document.pages) == 3
        last = document.pages[-1]
        assert last.span.start == last.span.end
        assert last.width > 0

    def test_pages_still_tile_the_text_around_an_empty_page(self) -> None:
        document = parse("mixed_pages.pdf")

        cursor = 0
        for page in document.pages:
            assert page.span.start == cursor
            cursor = page.span.end
        assert cursor == len(document.text)

    def test_a_fully_scanned_pdf_yields_no_tokens_rather_than_failing(self) -> None:
        # The native parser is not the right parser for this file. It says so by
        # producing an empty document, and the *routing* decision is what stops
        # this from happening in practice (US2), not the parser refusing.
        document = parse("scanned_contract.pdf")

        assert len(document.pages) == 2
        assert len(list(document.tokens)) == 0


class TestReadingOrder:
    """T030 — the R5 assumption, resolved by measurement.

    The plan hoped PyMuPDF's sorted extraction would deliver multi-column
    reading order. It does not: it sorts by vertical position across the whole
    page, so a two-column page comes out interleaved line by line. The adapter
    therefore uses unsorted (content-stream) order and declares exactly that.
    """

    def test_the_declared_order_names_what_is_actually_delivered(self) -> None:
        assert PdfTextParser().reading_order == "pymupdf-stream@1"

    def test_columns_are_not_interleaved(self) -> None:
        document = parse("two_column.pdf")
        text = document.text

        end_of_left = text.index("when the tokens are compared")
        start_of_right = text.index("The second column begins")

        assert end_of_left < start_of_right, "the second column leaked into the first"

    def test_sorted_extraction_would_interleave_them(self) -> None:
        """Why ``sort=True`` is not used — asserted, not just asserted in a comment.

        If a future PyMuPDF changes this, the test fails and the choice gets
        revisited deliberately rather than being inherited forever.
        """
        with pymupdf.open(FIXTURES / "two_column.pdf") as pdf:
            words = [word[4] for word in pdf[0].get_text("words", sort=True)]

        first_column_words = words.index("first")
        second_column_words = words.index("second")

        assert second_column_words - first_column_words < 10, (
            "sorted mode no longer interleaves columns; revisit the declared reading order"
        )


class TestRotation:
    """T031 — the R8 assumption, resolved by measurement.

    PyMuPDF reports word boxes in *unrotated* page space while ``page.rect`` is
    the page as displayed. Normalizing one against the other would put every box
    in the wrong place on a rotated page, so the adapter maps each box through
    ``page.rotation_matrix`` first.
    """

    def test_page_dimensions_are_the_displayed_ones(self) -> None:
        document = parse("rotated_90.pdf")
        page = document.pages[0]

        assert page.rotation == 90
        assert page.width > page.height, "a 90-degree rotated A4 page displays landscape"

    def test_geometry_describes_the_page_as_displayed(self) -> None:
        # The same word sits at the left edge of the unrotated page. Rotated 90
        # degrees for display it belongs at the *right* edge. Skipping the
        # rotation would put it at x0 ~ 0.07; applying it puts it at ~0.90, so
        # this assertion is exactly the regression test for forgetting it.
        upright = parse("digital_invoice.pdf")
        rotated = parse("rotated_90.pdf")

        (upright_box,) = upright.locate(upright.find("ACME")[0])
        (rotated_box,) = rotated.locate(rotated.find("ACME")[0])

        assert upright_box.bbox.x0 < 0.2
        assert rotated_box.bbox.x0 > 0.8

    def test_rotated_geometry_stays_normalized(self) -> None:
        document = parse("rotated_90.pdf")

        for token in document.tokens:
            assert token.geometry is not None
            for value in token.geometry.bbox:
                assert 0.0 <= value <= 1.0


class TestFailures:
    def test_encrypted_pdf_is_refused_explicitly(self) -> None:
        with pytest.raises(UnsupportedDocumentError) as caught:
            parse("encrypted.pdf")

        assert caught.value.reason == "encrypted"
        assert caught.value.blob_id is not None

    def test_truncated_pdf_is_refused_explicitly(self) -> None:
        source = source_for("digital_invoice.pdf")
        truncated = SourceFile.from_bytes(source.data[:60])

        with pytest.raises(UnsupportedDocumentError) as caught:
            PdfTextParser().parse(truncated, {}, TransportSettings())

        assert caught.value.reason == "corrupt"

    def test_no_library_exception_escapes(self) -> None:
        from docdoc.kernel import DocdocError

        source = SourceFile.from_bytes(b"%PDF-1.7\nnot really a pdf body")

        with pytest.raises(DocdocError):
            PdfTextParser().parse(source, {}, TransportSettings())


class TestDeterminismAndIdentity:
    def test_two_parses_agree_completely(self) -> None:
        first = parse("digital_invoice.pdf")
        second = parse("digital_invoice.pdf")

        assert first.id == second.id
        assert first.text == second.text
        assert list(first.tokens) == list(second.tokens)

    def test_options_change_identity(self) -> None:
        source = source_for("digital_invoice.pdf")
        parser = PdfTextParser()

        plain = parser.parse(source, {}, TransportSettings())
        with_option = parser.parse(source, {"flavour": "verbose"}, TransportSettings())

        assert plain.id != with_option.id

    def test_transport_settings_do_not_change_identity(self) -> None:
        # ING-5/SC-018: a value that cannot change the content of a result must
        # not be able to change its identity.
        source = source_for("digital_invoice.pdf")
        parser = PdfTextParser()

        default = parser.parse(source, {}, TransportSettings())
        impatient = parser.parse(source, {}, TransportSettings(deadline_s=1.0, max_attempts=1))

        assert default.id == impatient.id
