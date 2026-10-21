"""The PaddleX adapter keeps each page's turn beside the `Document` (020)."""

from __future__ import annotations

import base64
import io
import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pypdfium2", reason="splitting a PDF needs the docdoc[ocr] extra")

import pypdfium2 as pdfium

from docdoc.ingest.options import TransportSettings
from docdoc.ingest.parsers.paddlex import OcrEndpoint, PaddleXParser
from docdoc.ingest.source import SourceFile

FIXTURES = Path(__file__).parent.parent / "fixtures"
TRANSPORT = TransportSettings(max_attempts=1, deadline_s=5.0)
IMAGE = SourceFile.from_bytes((FIXTURES / "image" / "sample_page.png").read_bytes())


def _two_page_pdf() -> bytes:
    source = pdfium.PdfDocument(FIXTURES / "pdf" / "digital_invoice.pdf")
    document = pdfium.PdfDocument.new()
    document.import_pages(source, pages=[0, 0])
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _parser(post: Any, *, max_pages: int | None = None) -> PaddleXParser:
    endpoint = OcrEndpoint(
        id="paddle", url="http://paddle.example/ocr", version="1.0.0", max_pages=max_pages
    )
    return PaddleXParser(endpoint, post=post)


def _page(text: str, raw_angle: Any) -> dict[str, Any]:
    pruned: dict[str, Any] = {"rec_texts": [text], "rec_boxes": [[0, 0, 10, 10]]}
    if raw_angle is not None:
        pruned["doc_preprocessor_res"] = {"angle": raw_angle}
    return {"prunedResult": pruned}


def _pdf_response(raw_angles: list[Any]) -> dict[str, Any]:
    return {
        "errorCode": 0,
        "errorMsg": "Success",
        "result": {
            "dataInfo": {
                "type": "pdf",
                "pages": [{"width": 100.0, "height": 100.0} for _ in raw_angles],
            },
            "ocrResults": [_page(f"p{i}", a) for i, a in enumerate(raw_angles)],
        },
    }


def _image_response(raw_angle: Any) -> dict[str, Any]:
    return {
        "errorCode": 0,
        "errorMsg": "Success",
        "result": {
            "dataInfo": {"type": "image", "width": 100.0, "height": 100.0},
            "ocrResults": [_page("hello", raw_angle)],
        },
    }


def test_the_recorded_quarter_turn_pins_the_sign_convention() -> None:
    """`rotated_90.pdf` is upright content shown a quarter turn clockwise
    (`/Rotate 90`); real PaddleX reported `angle: 90` and boxes in the upright
    frame. Straightening it is a quarter turn *counter*-clockwise: 270 clockwise.
    """
    recorded = json.loads((FIXTURES / "paddlex" / "paddle.rotated_90.json").read_text())
    source = SourceFile.from_bytes((FIXTURES / "pdf" / "rotated_90.pdf").read_bytes())
    parser = _parser(lambda endpoint, body, timeout_s: recorded)

    document, angles = parser.parse_with_orientation(source, {}, TRANSPORT)

    assert angles == (270,)
    assert len(document.pages) == 1


@pytest.mark.parametrize(("raw", "viewer"), [(0, 0), (90, 270), (180, 180), (270, 90)])
def test_paddlex_reports_one_angle_per_page(raw: int, viewer: int) -> None:
    parser = _parser(lambda endpoint, body, timeout_s: _image_response(raw))

    document, angles = parser.parse_with_orientation(IMAGE, {}, TRANSPORT)

    assert angles == (viewer,)
    assert document == parser.parse(IMAGE, {}, TRANSPORT)


@pytest.mark.parametrize("raw", [45, None, "90", -90, 360])
def test_unknown_angle_value_means_zero(raw: Any) -> None:
    parser = _parser(lambda endpoint, body, timeout_s: _image_response(raw))

    assert parser.parse_with_orientation(IMAGE, {}, TRANSPORT)[1] == (0,)


def test_angles_join_across_pdf_chunks() -> None:
    raw_per_chunk = iter([[270], [180]])

    def fake_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
        assert len(pdfium.PdfDocument(base64.b64decode(body["file"]))) == 1
        return _pdf_response(next(raw_per_chunk))

    source = SourceFile.from_bytes(_two_page_pdf(), filename="two.pdf")
    document, angles = _parser(fake_post, max_pages=1).parse_with_orientation(source, {}, TRANSPORT)

    assert angles == (90, 180)
    assert len(document.pages) == 2


def test_document_bytes_unchanged() -> None:
    parser = _parser(lambda endpoint, body, timeout_s: _image_response(90))

    oriented, _ = parser.parse_with_orientation(IMAGE, {}, TRANSPORT)
    plain = parser.parse(IMAGE, {}, TRANSPORT)

    assert oriented.model_dump_json() == plain.model_dump_json()
    assert oriented.id == plain.id
