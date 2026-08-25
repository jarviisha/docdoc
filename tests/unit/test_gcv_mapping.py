"""The Cloud Vision response-to-IR mapping, pinned by a recorded response.

Runs with no credentials and no network. Unlike the Azure mapping tests, there is
no ``importorskip`` here: ``docdoc.ingest.parsers.gcv`` imports its SDK inside the
method that reaches the network, so the mapping half is importable and testable on
a base install. That is the stronger position -- this adapter is exercised in every
environment rather than only in a credentialed one (Constitution XII, SC-009,
research.md R14).
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from docdoc.ingest.errors import ParserError, ProviderError, UnsupportedDocumentError
from docdoc.ingest.parsers.gcv import GoogleCloudVisionParser, map_annotate_result
from docdoc.ingest.source import SourceFile
from docdoc.ingest.validate import validate_output

FIXTURES = Path(__file__).parent.parent / "fixtures"
RECORDED = FIXTURES / "gcv" / "sample_page.annotate.json"

#: The fixture's page, in pixels, so the geometry assertions state what they mean.
PAGE_WIDTH = 910
PAGE_HEIGHT = 1287


@pytest.fixture
def recorded() -> dict[str, Any]:
    return json.loads(RECORDED.read_text())


@pytest.fixture
def source() -> SourceFile:
    path = FIXTURES / "image" / "sample_page.png"
    return SourceFile.from_bytes(path.read_bytes(), filename=path.name)


def mapped(result: dict[str, Any], source: SourceFile):  # type: ignore[no-untyped-def]
    return map_annotate_result(result, source=source, options={}, text_layer=None)


def words_of(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Every word in the fixture, in the order the service emitted them."""
    return [
        word
        for page in result["fullTextAnnotation"]["pages"]
        for block in page["blocks"]
        for paragraph in block["paragraphs"]
        for word in paragraph["words"]
    ]


class TestShape:
    def test_produces_a_valid_document(self, recorded: dict[str, Any], source: SourceFile) -> None:
        document = mapped(recorded, source)

        assert document.text == "Invoice INV-001\nTotal 228.00\n"
        assert len(document.pages) == 1

    def test_an_image_yields_a_single_page(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = mapped(recorded, source)
        (page,) = document.pages

        assert (page.width, page.height) == (PAGE_WIDTH, PAGE_HEIGHT)
        assert page.rotation == 0

    def test_pages_tile_the_text(self, recorded: dict[str, Any], source: SourceFile) -> None:
        document = mapped(recorded, source)

        cursor = 0
        for page in document.pages:
            assert page.span.start == cursor
            cursor = page.span.end
        assert cursor == len(document.text)

    def test_tokens_index_into_the_assembled_text(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = mapped(recorded, source)
        expected = ["".join(s["text"] for s in word["symbols"]) for word in words_of(recorded)]

        for token, word in zip(document.tokens, expected, strict=True):
            assert document.text[token.span.start : token.span.end] == word

    def test_a_value_resolves_to_a_page_and_a_box(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = mapped(recorded, source)

        (span,) = document.find("INV-001")
        (geometry,) = document.locate(span)

        assert geometry.page_index == 0

    def test_the_parsers_own_output_checks_pass(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # The ingest layer holds a parser to what it declared. Running those
        # checks here means a declaration drifting from the mapping is caught by
        # the offline suite rather than only by a live parse.
        document = mapped(recorded, source)

        validate_output(
            document,
            GoogleCloudVisionParser.capabilities,
            parser_id="gcv",
            blob_id=source.blob_id,
            parser_version=document.provenance.parser_version,
        )


class TestTextIsAssembledNotAdopted:
    """The service's own text string is deliberately unused (research.md R6).

    It carries no offsets, so every token would have to be *found* in it. These
    tests pin that the correspondence is built rather than recovered -- if someone
    later "optimizes" by adopting `fullTextAnnotation.text`, they fail.
    """

    def test_the_service_text_field_does_not_decide_the_document_text(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        recorded["fullTextAnnotation"]["text"] = "TOTALLY DIFFERENT TEXT"

        assert mapped(recorded, source).text == "Invoice INV-001\nTotal 228.00\n"

    def test_removing_the_service_text_changes_nothing(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        del recorded["fullTextAnnotation"]["text"]

        assert mapped(recorded, source).text == "Invoice INV-001\nTotal 228.00\n"


class TestLineStructure:
    def test_a_line_break_splits_one_paragraph(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # The fixture holds all four words in a single paragraph. The service
        # reports the wrap only as a break marker on "INV-001", so that marker is
        # the only thing standing between two lines and one run-on line.
        document = mapped(recorded, source)

        assert document.text.count("\n") == 2
        assert "INV-001\nTotal" in document.text

    def test_a_hyphen_break_does_not_end_a_line(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # Dehyphenation is a downstream decision. The adapter neither rejoins the
        # word nor treats the hyphen as a line ending (FR-007).
        words = words_of(recorded)
        words[0]["symbols"][-1]["property"] = {"detectedBreak": {"type": "HYPHEN"}}
        document = mapped(recorded, source)

        assert "Invoice INV-001" in document.text

    def test_an_unmarked_paragraph_still_ends_its_line(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # Without this, two paragraphs the service showed as separate would run
        # together, and the builder's line break is the only separator the
        # assembled text has.
        page = recorded["fullTextAnnotation"]["pages"][0]
        first, second = copy.deepcopy(page["blocks"][0]), copy.deepcopy(page["blocks"][0])
        for block in (first, second):
            for word in block["paragraphs"][0]["words"]:
                word["symbols"][-1].pop("property", None)
        page["blocks"] = [first, second]

        document = mapped(recorded, source)

        assert document.text.count("\n") == 2


class TestGeometry:
    def test_coordinates_are_normalized(self, recorded: dict[str, Any], source: SourceFile) -> None:
        document = mapped(recorded, source)

        for token in document.tokens:
            assert token.geometry is not None
            for value in token.geometry.bbox:
                assert 0.0 <= value <= 1.0

    def test_the_service_pixel_unit_does_not_survive(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = mapped(recorded, source)
        first = next(iter(document.tokens))

        assert first.geometry is not None
        assert first.geometry.bbox.x0 == pytest.approx(91 / PAGE_WIDTH)
        assert first.geometry.bbox.y0 == pytest.approx(110 / PAGE_HEIGHT)

    def test_a_skewed_quadrilateral_becomes_its_enclosing_box(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        words_of(recorded)[0]["boundingBox"]["vertices"] = [
            {"x": 91, "y": 110},
            {"x": 180, "y": 104},
            {"x": 184, "y": 136},
            {"x": 95, "y": 142},
        ]
        document = mapped(recorded, source)
        first = next(iter(document.tokens))

        assert first.geometry is not None
        assert first.geometry.bbox.x1 == pytest.approx(184 / PAGE_WIDTH)
        assert first.geometry.bbox.y1 == pytest.approx(142 / PAGE_HEIGHT)

    def test_an_omitted_zero_coordinate_reads_as_zero(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # The service omits a vertex coordinate entirely when it is 0 rather than
        # sending `0` — a protobuf default that does not survive the JSON round
        # trip. A word touching the left edge is exactly that case.
        for vertex in words_of(recorded)[0]["boundingBox"]["vertices"]:
            vertex.pop("x", None)
        document = mapped(recorded, source)
        first = next(iter(document.tokens))

        assert first.geometry is not None
        assert first.geometry.bbox.x0 == 0.0
        assert first.geometry.bbox.x1 == 0.0

    def test_normalized_vertices_are_not_divided_again(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # The other vertex form the service can send. Dividing these by the page
        # dimensions would collapse every box into the top-left corner.
        word = words_of(recorded)[0]
        word["boundingBox"] = {
            "normalizedVertices": [
                {"x": 0.1, "y": 0.2},
                {"x": 0.4, "y": 0.2},
                {"x": 0.4, "y": 0.3},
                {"x": 0.1, "y": 0.3},
            ]
        }
        document = mapped(recorded, source)
        first = next(iter(document.tokens))

        assert first.geometry is not None
        assert first.geometry.bbox.x0 == pytest.approx(0.1)
        assert first.geometry.bbox.x1 == pytest.approx(0.4)

    def test_a_word_without_a_box_is_reported_not_emitted(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # Geometry is all-or-nothing (ING-4) and this parser declares it, so one
        # boxless word makes the whole declaration false.
        del words_of(recorded)[1]["boundingBox"]

        with pytest.raises(ParserError) as caught:
            mapped(recorded, source)

        assert caught.value.reason == "internal"
        assert "no bounding box" in str(caught.value)

    def test_geometry_off_the_page_is_refused(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        words_of(recorded)[0]["boundingBox"]["vertices"] = [
            {"x": 91, "y": 110},
            {"x": PAGE_WIDTH * 2, "y": 110},
            {"x": PAGE_WIDTH * 2, "y": 132},
            {"x": 91, "y": 132},
        ]

        with pytest.raises(ParserError) as caught:
            mapped(recorded, source)

        assert caught.value.reason == "internal"
        assert "outside page 0" in str(caught.value)


class TestProvenance:
    def test_records_the_service_path(self, recorded: dict[str, Any], source: SourceFile) -> None:
        document = mapped(recorded, source)

        assert document.provenance.parser_id == "gcv"
        assert document.provenance.text_layer_used is False
        assert document.provenance.reading_order == "gcv-block-order@1"

    def test_service_confidence_is_stored_verbatim(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # Passed through, not interpreted -- the treatment ADR-0004 gives any
        # provider's self-reported confidence.
        document = mapped(recorded, source)
        first = next(iter(document.tokens))

        assert first.source_confidence == pytest.approx(0.99)

    def test_a_missing_confidence_is_none_not_zero(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # Zero would read as "the service was certain this is wrong", which is a
        # different claim from "the service did not say".
        del words_of(recorded)[0]["confidence"]
        document = mapped(recorded, source)

        assert next(iter(document.tokens)).source_confidence is None


class TestEmptyAndFailedResponses:
    def test_no_annotation_is_refused_rather_than_returned_empty(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # A blank image is a real outcome, but the response then carries no page
        # dimensions, so there is no page to report and nothing downstream could
        # be located against.
        del recorded["fullTextAnnotation"]

        with pytest.raises(ParserError) as caught:
            mapped(recorded, source)

        assert caught.value.reason == "empty_result"

    def test_an_annotation_without_pages_is_refused(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        recorded["fullTextAnnotation"]["pages"] = []

        with pytest.raises(ParserError) as caught:
            mapped(recorded, source)

        assert caught.value.reason == "empty_result"

    @pytest.mark.parametrize("code", [3, 9])
    def test_a_rejected_image_is_unsupported_not_a_provider_failure(
        self, recorded: dict[str, Any], source: SourceFile, code: int
    ) -> None:
        # A per-image error travels inside a 200 response, so it never reaches the
        # exception translation on the wire path.
        recorded["error"] = {"code": code, "message": "Bad image data"}

        with pytest.raises(UnsupportedDocumentError) as caught:
            mapped(recorded, source)

        assert caught.value.reason == "corrupt"
        assert caught.value.parser_id == "gcv"

    @pytest.mark.parametrize(("code", "reason"), [(7, "auth"), (16, "auth"), (8, "rate_limit")])
    def test_a_service_status_becomes_a_provider_error(
        self, recorded: dict[str, Any], source: SourceFile, code: int, reason: str
    ) -> None:
        recorded["error"] = {"code": code, "message": "denied"}

        with pytest.raises(ProviderError) as caught:
            mapped(recorded, source)

        assert caught.value.reason == reason

    def test_the_service_message_is_not_carried_into_the_error(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # The prose can quote the request, and a value from a document is document
        # content (FR-029).
        recorded["error"] = {"code": 8, "message": "quota exceeded for INV-001"}

        with pytest.raises(ProviderError) as caught:
            mapped(recorded, source)

        assert "INV-001" not in str(caught.value)


class TestNoServiceTypesLeak:
    def test_no_service_field_name_appears_in_the_document(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        document = mapped(recorded, source)
        rendered = repr(document)

        for leaked in ("boundingBox", "normalizedVertices", "detectedBreak", "fullTextAnnotation"):
            assert leaked not in rendered

    def test_an_unexpected_response_shape_leaves_as_a_docdoc_error(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        # A renamed field or a null where a number belongs is a provider-shaped
        # failure, and it may not cross the public API as one. Here the walk down
        # to the words finds nothing it recognizes, which surfaces as an empty
        # result rather than as a page of silence.
        recorded["fullTextAnnotation"]["pages"][0]["blocks"] = [{"paragraphs": None}]

        with pytest.raises(ParserError) as caught:
            mapped(recorded, source)

        assert caught.value.reason == "empty_result"

    def test_a_null_where_a_number_belongs_stays_in_the_error_model(
        self, recorded: dict[str, Any], source: SourceFile
    ) -> None:
        words_of(recorded)[0]["confidence"] = "not a number"

        with pytest.raises(ParserError) as caught:
            mapped(recorded, source)

        assert caught.value.reason == "internal"
        assert caught.value.detail == "ValueError"
