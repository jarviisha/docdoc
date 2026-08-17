"""T042 — the response-to-IR mapping, pinned by recorded responses.

These run with no credentials and no network, which is the point: without them
the adapter that produces every scanned-document result would only ever be
exercised in a credentialed environment, and would regress silently everywhere
else (research.md R14, SC-009).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docdoc.ingest.errors import ParserError
from docdoc.ingest.parsers.azure_di import map_analyze_result
from docdoc.ingest.source import SourceFile

FIXTURES = Path(__file__).parent.parent / "fixtures"
RECORDED = FIXTURES / "azure" / "scanned_contract.analyze.json"


@pytest.fixture
def recorded() -> dict[str, Any]:
    return json.loads(RECORDED.read_text())


@pytest.fixture
def source() -> SourceFile:
    path = FIXTURES / "pdf" / "scanned_contract.pdf"
    return SourceFile.from_bytes(path.read_bytes(), filename=path.name)


def mapped(result: dict[str, Any], source: SourceFile):  # type: ignore[no-untyped-def]
    return map_analyze_result(result, source=source, options={}, text_layer=None)


class TestShape:
    def test_produces_a_valid_document(self, recorded: dict[str, Any], source: SourceFile) -> None:
        document = mapped(recorded, source)

        assert document.text.startswith("SERVICE AGREEMENT")
        assert len(document.pages) == 2

    def test_pages_tile_the_text(self, recorded: dict[str, Any], source: SourceFile) -> None:
        # The service leaves the newline between pages outside both spans; the
        # adapter closes the gap rather than handing the kernel a hole.
        document = mapped(recorded, source)

        cursor = 0
        for page in document.pages:
            assert page.span.start == cursor
            cursor = page.span.end
        assert cursor == len(document.text)

    def test_tokens_index_into_the_service_text(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = mapped(recorded, source)

        for token, word in zip(
            document.tokens,
            [word for page in recorded["pages"] for word in page["words"]],
            strict=True,
        ):
            assert document.text[token.span.start : token.span.end] == word["content"]

    def test_a_value_resolves_to_a_page_and_a_box(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = mapped(recorded, source)

        (span,) = document.find("Schedule")
        (geometry,) = document.locate(span)

        assert geometry.page_index == 1


class TestGeometry:
    def test_coordinates_are_normalized(self, recorded: dict[str, Any], source: SourceFile) -> None:
        document = mapped(recorded, source)

        for token in document.tokens:
            assert token.geometry is not None
            for value in token.geometry.bbox:
                assert 0.0 <= value <= 1.0

    def test_the_service_unit_does_not_survive(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # The response is in inches. Nothing in the document may still be.
        document = mapped(recorded, source)
        (token,) = [t for t in document.tokens if t.geometry][:1]

        assert token.geometry is not None
        assert token.geometry.bbox.x0 < 0.2, "an inch value would be far outside 0..1"

    def test_a_rotated_polygon_becomes_its_enclosing_box(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # Skewed text comes back as a rotated quadrilateral. A box that
        # certainly contains the glyphs beats a tighter one that might not.
        recorded["pages"][0]["words"][0]["polygon"] = [1.0, 1.0, 2.0, 0.9, 2.1, 1.3, 1.1, 1.4]
        document = mapped(recorded, source)
        first = next(iter(document.tokens))

        assert first.geometry is not None
        width = recorded["pages"][0]["width"]
        assert first.geometry.bbox.x0 == pytest.approx(1.0 / width)
        assert first.geometry.bbox.x1 == pytest.approx(2.1 / width)


class TestTables:
    def test_tables_are_retained_with_placeable_cells(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = mapped(recorded, source)

        (table,) = document.tables
        assert (table.n_rows, table.n_columns) == (2, 2)
        assert len(table.cells) == 4

    def test_cells_resolve_to_their_text(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = mapped(recorded, source)
        (table,) = document.tables

        contents = {document.text[cell.span.start : cell.span.end] for cell in table.cells}
        assert contents == {"Item", "Qty", "Widgets", "4"}

    def test_a_table_without_a_span_is_reported_not_dropped(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        """An unanchored table is a half-fact — and so is quietly discarding it.

        This used to drop the table and return successfully, which meant a caller
        could not tell a document that has no tables from one whose tables could
        not be placed. Rejecting says which of the two happened.
        """
        del recorded["tables"][0]["spans"]

        with pytest.raises(ParserError) as caught:
            mapped(recorded, source)

        assert caught.value.reason == "internal"
        assert "no text anchor" in str(caught.value)

    def test_no_tables_is_a_normal_condition(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        recorded["tables"] = []

        assert mapped(recorded, source).tables == ()


class TestProvenance:
    def test_records_the_service_path(self, recorded: dict[str, Any], source: SourceFile) -> None:
        document = mapped(recorded, source)

        assert document.provenance.parser_id == "azure-di"
        assert document.provenance.text_layer_used is False
        assert document.provenance.reading_order == "azure-di-service@1"

    def test_service_confidence_is_stored_verbatim(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # Passed through, not interpreted -- the same treatment ADR-0004 gives a
        # model's self-reported confidence.
        document = mapped(recorded, source)
        first = next(iter(document.tokens))

        assert first.source_confidence == pytest.approx(0.99)


class TestNoServiceTypesLeak:
    def test_no_service_field_name_appears_in_the_document(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = mapped(recorded, source)
        rendered = repr(document)

        for leaked in ("polygon", "boundingRegions", "pageNumber", "apiVersion", "modelId"):
            assert leaked not in rendered

    def test_the_document_is_built_from_kernel_types_only(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # A dict from the SDK smuggled into a field would satisfy a string
        # search and still be a leak, so check the types themselves.
        document = mapped(recorded, source)

        assert all(type(page).__module__.startswith("docdoc.kernel") for page in document.pages)
        assert all(type(table).__module__.startswith("docdoc.kernel") for table in document.tables)


class TestMalformedResponses:
    def test_words_out_of_order_are_reported_not_sorted(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        words = recorded["pages"][0]["words"]
        words[0], words[1] = words[1], words[0]

        with pytest.raises(ParserError) as caught:
            mapped(recorded, source)

        assert caught.value.reason == "invalid_order"
        assert caught.value.parser_id == "azure-di"

    def test_content_without_pages_is_rejected(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        recorded["pages"] = []

        with pytest.raises(ParserError) as caught:
            mapped(recorded, source)

        assert caught.value.reason == "empty_result"

    def test_an_empty_result_is_an_empty_document_not_a_crash(self, source: SourceFile) -> None:
        document = mapped({"content": "", "pages": []}, source)

        assert document.text == ""
        assert len(list(document.tokens)) == 0
