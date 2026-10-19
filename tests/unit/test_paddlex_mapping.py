"""The PaddleX `/ocr` response-to-IR mapping, pinned by recorded responses.

Runs with no network and no live service, on the same reasoning
`test_gcv_mapping.py` gives for its own fixture: `pages_of` and
`build_document` are pure, so the recorded JSON exercises the real mapping in
every environment rather than only one with a tunnel open to the OCR host
(ADR-0021, Constitution XII, R14).

Every recorded fixture under `tests/fixtures/paddlex/` was trimmed by
`tests/fixtures/paddlex/record.py` to the handful of fields this mapping
reads -- no base64 image ever made it into a committed file.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from docdoc.ingest.capabilities import ParserCapabilities
from docdoc.ingest.errors import ParserError, ProviderError
from docdoc.ingest.parsers.paddlex import READING_ORDER, build_document, pages_of
from docdoc.ingest.source import JPEG, PDF, PNG, SourceFile
from docdoc.ingest.validate import validate_output
from docdoc.kernel import Document

FIXTURES = Path(__file__).parent.parent / "fixtures"
PADDLEX_FIXTURES = FIXTURES / "paddlex"

#: What a configured PaddleX-protocol endpoint declares. Line-level OCR, no
#: table structure, geometry always present -- `build_document` always calls
#: `normalize_bbox`, so a parser claiming anything else would be dishonest
#: about its own output (ING-4).
CAPABILITIES = ParserCapabilities(
    text=True,
    geometry=True,
    tables=False,
    handwriting=False,
    media_types=frozenset({PDF, JPEG, PNG}),
    requires_network=True,
)

#: Not a real blob -- `pages_of` never reads the bytes it is paired with. A
#: fixed, well-formed id is enough for the error-model tests that need one at
#: all.
DUMMY_BLOB_ID = "sha256:" + "0" * 64


def _recorded(name: str) -> dict[str, Any]:
    return json.loads((PADDLEX_FIXTURES / f"{name}.json").read_text())


def _source(filename: str) -> SourceFile:
    path = (
        (FIXTURES / "pdf" / filename)
        if filename.endswith(".pdf")
        else (FIXTURES / "image" / filename)
    )
    return SourceFile.from_bytes(path.read_bytes(), filename=filename)


def _mapped(response: dict[str, Any], source: SourceFile, *, parser_id: str = "paddle") -> Document:
    pages = pages_of(response, parser_id=parser_id, blob_id=source.blob_id)
    return build_document(
        pages,
        source=source,
        options={},
        text_layer=None,
        parser_id=parser_id,
        parser_version="1.0.0+paddle-test",
        capabilities=CAPABILITIES,
    )


class TestInvoice:
    def test_invoice_maps_to_valid_document(self) -> None:
        recorded = _recorded("paddle.digital_invoice")
        document = _mapped(recorded, _source("digital_invoice.pdf"))

        validate_output(
            document,
            CAPABILITIES,
            parser_id="paddle",
            blob_id=document.source.blob_id,
            parser_version=document.provenance.parser_version,
        )
        assert "INV-001" in document.text
        assert len(document.pages) == 1

    def test_each_line_is_one_token(self) -> None:
        recorded = _recorded("paddle.digital_invoice")
        rec_texts = recorded["result"]["ocrResults"][0]["prunedResult"]["rec_texts"]

        document = _mapped(recorded, _source("digital_invoice.pdf"))

        assert len(document.tokens) == len(rec_texts)

    def test_boxes_are_normalized_against_datainfo_page_size(self) -> None:
        recorded = _recorded("paddle.digital_invoice")
        pruned = recorded["result"]["ocrResults"][0]["prunedResult"]
        page_size = recorded["result"]["dataInfo"]["pages"][0]
        width, height = page_size["width"], page_size["height"]
        x1, y1, x2, y2 = pruned["rec_boxes"][0]

        document = _mapped(recorded, _source("digital_invoice.pdf"))
        first = next(iter(document.tokens))

        assert first.geometry is not None
        assert first.geometry.bbox.x0 == pytest.approx(x1 / width)
        assert first.geometry.bbox.y0 == pytest.approx(y1 / height)
        assert first.geometry.bbox.x1 == pytest.approx(x2 / width)
        assert first.geometry.bbox.y1 == pytest.approx(y2 / height)

    def test_scores_become_source_confidence(self) -> None:
        recorded = _recorded("paddle.digital_invoice")
        first_score = recorded["result"]["ocrResults"][0]["prunedResult"]["rec_scores"][0]

        document = _mapped(recorded, _source("digital_invoice.pdf"))
        first = next(iter(document.tokens))

        assert first.source_confidence == pytest.approx(first_score)

    def test_missing_scores_are_none(self) -> None:
        # The Tesseract shim's recorded response has no `rec_scores` key at all.
        recorded = _recorded("tesseract-shim.digital_invoice")

        document = _mapped(recorded, _source("digital_invoice.pdf"), parser_id="tesseract")

        assert recorded["result"]["ocrResults"][0]["prunedResult"].get("rec_scores") is None
        assert all(token.source_confidence is None for token in document.tokens)


class TestImageResponse:
    def test_image_response_uses_top_level_size(self) -> None:
        recorded = _recorded("paddle.sample_page")
        data_info = recorded["result"]["dataInfo"]
        assert data_info["type"] == "image"

        document = _mapped(recorded, _source("sample_page.png"))
        (page,) = document.pages

        assert (page.width, page.height) == (data_info["width"], data_info["height"])
        assert page.rotation == 0


class TestMultiPage:
    def test_multi_page_keeps_page_order(self) -> None:
        recorded = _recorded("paddle.scanned_contract")

        document = _mapped(recorded, _source("scanned_contract.pdf"))

        assert len(document.pages) == 2
        assert document.pages[0].span.start < document.pages[1].span.start
        # Page 1's last line precedes page 2's content in the assembled text --
        # the order the service emitted them in, not a layout reconstruction.
        # ("Schedule A" itself appears on both pages -- once mid-sentence on
        # page 1 -- so "Widgets, assorted" is the marker unique to page 2.)
        assert document.text.index("written notice") < document.text.index("Widgets, assorted")

    def test_blank_middle_page_is_kept(self) -> None:
        # Review Focus 2: a blank page is kept, with an empty span, so page
        # indices downstream stay true to the pages that were actually sent.
        recorded = _recorded("paddle.blank_page")

        document = _mapped(recorded, _source("scanned_contract.pdf"))

        assert len(document.pages) == 2
        assert document.pages[1].span.start == document.pages[1].span.end

    def test_all_pages_blank_is_empty_result(self) -> None:
        # Only when *every* page is blank does this become a refusal rather than
        # a document that happens to have an empty page in it.
        recorded = copy.deepcopy(_recorded("paddle.blank_page"))
        first_page = recorded["result"]["ocrResults"][0]["prunedResult"]
        first_page["rec_texts"] = []
        first_page["rec_boxes"] = []
        first_page["rec_scores"] = []

        with pytest.raises(ParserError) as caught:
            _mapped(recorded, _source("scanned_contract.pdf"))

        assert caught.value.reason == "empty_result"


class TestRotatedPage:
    def test_rotated_page_stays_on_page(self) -> None:
        # Review Focus 1: `dataInfo` names the page *as rendered* (landscape,
        # 1684x1190), before PaddleX's own doc_preprocessor_res straightens it
        # for display (angle: 90). `rec_boxes` describes that straightened,
        # portrait frame -- the adapter must swap width and height back
        # itself, or the box normalizes against the wrong aspect ratio
        # without raising anything: a value can land inside [0, 1] and still
        # describe the wrong rectangle. So "every bbox in [0, 1]" alone is a
        # postcondition of `normalize_bbox`, not evidence this adapter chose
        # the right frame to normalize against -- the assertion below instead
        # checks the *rectangle*, against the one independent source of truth
        # available: the same invoice, recognized unrotated.
        rotated = _recorded("paddle.rotated_90")
        assert (
            rotated["result"]["ocrResults"][0]["prunedResult"]["doc_preprocessor_res"]["angle"]
            == 90
        )

        invoice = _recorded("paddle.digital_invoice")
        invoice_pruned = invoice["result"]["ocrResults"][0]["prunedResult"]
        invoice_size = invoice["result"]["dataInfo"]["pages"][0]
        x1, y1, x2, y2 = invoice_pruned["rec_boxes"][0]
        expected = (
            x1 / invoice_size["width"],
            y1 / invoice_size["height"],
            x2 / invoice_size["width"],
            y2 / invoice_size["height"],
        )

        document = _mapped(rotated, _source("rotated_90.pdf"))
        (page,) = document.pages
        first = next(iter(document.tokens))

        # Portrait, like the unrotated invoice -- not the landscape size
        # `dataInfo` reported before doc_preprocessor_res corrected it.
        assert page.width < page.height
        assert first.geometry is not None
        # Pixel-level OCR jitter (a line or two of difference between the two
        # recognition runs), not exact equality -- but tight enough that the
        # pre-fix bug (dividing by the unswapped, landscape size) fails this
        # by roughly an order of magnitude more than jitter alone explains.
        assert first.geometry.bbox == pytest.approx(expected, abs=0.01)


class TestErrorModel:
    def test_error_code_in_200_body_is_provider_error(self) -> None:
        # Review Focus 4: the old shim's failure style -- a 200 whose body
        # carries a nonzero errorCode and the exception class name as the message.
        response = {"errorCode": 500, "errorMsg": "ValueError"}

        with pytest.raises(ProviderError) as caught:
            pages_of(response, parser_id="tesseract", blob_id=DUMMY_BLOB_ID)

        assert caught.value.reason == "service"

    def test_length_mismatch_is_parser_error(self) -> None:
        response = {
            "errorCode": 0,
            "errorMsg": "Success",
            "result": {
                "ocrResults": [
                    {
                        "prunedResult": {
                            "rec_texts": ["one", "two"],
                            "rec_boxes": [[0, 0, 1, 1]],
                            "rec_scores": [0.9, 0.9],
                        }
                    }
                ],
                "dataInfo": {"type": "image", "width": 100, "height": 100},
            },
        }

        with pytest.raises(ParserError) as caught:
            pages_of(response, parser_id="paddle", blob_id=DUMMY_BLOB_ID)

        assert caught.value.reason == "internal"


class TestProvenance:
    def test_provenance_names_endpoint(self) -> None:
        recorded = _recorded("paddle.digital_invoice")

        document = _mapped(recorded, _source("digital_invoice.pdf"), parser_id="paddle")

        assert document.provenance.parser_id == "paddle"
        assert document.provenance.reading_order == READING_ORDER == "paddlex-line-order@1"
