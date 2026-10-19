"""Task 4 — a caller may name a configured parser; docdoc never falls back
(ADR-0021 §1, §2).

The property under test is the refusal, not the happy path alone: an unknown
name, an unavailable one, and one that cannot serve the document's media type
all raise ``ParserCapabilityError`` naming why, and never silently hand back a
*different* parser's result instead -- the whole reason ``select_named`` is a
separate method from ``select`` rather than ``select`` with an optional id.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pymupdf")
pytest.importorskip("pypdfium2")

from docdoc.ingest import parse
from docdoc.ingest.errors import ParserCapabilityError
from docdoc.ingest.parse import plan_parse
from docdoc.ingest.parsers.paddlex import ENDPOINTS_FILE_ENV, OcrEndpoint, PaddleXParser
from docdoc.ingest.parsers.pdf_text import PdfTextParser
from docdoc.ingest.registry import ParserRegistry, default_registry
from docdoc.ingest.source import JPEG, PDF, PNG

FIXTURES = Path(__file__).parent.parent / "fixtures"
PDF_FIXTURES = FIXTURES / "pdf"
IMAGE_FIXTURES = FIXTURES / "image"

INVOICE = (PDF_FIXTURES / "digital_invoice.pdf").read_bytes()
IMAGE = (IMAGE_FIXTURES / "sample_page.png").read_bytes()


def _endpoint(
    *,
    endpoint_id: str = "paddle",
    media_types: frozenset[str] | None = None,
) -> OcrEndpoint:
    return OcrEndpoint(
        id=endpoint_id,
        url=f"http://{endpoint_id}.example/ocr",
        version="1.0.0",
        media_types=media_types if media_types is not None else frozenset({PDF, JPEG, PNG}),
    )


def _refuse_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
    raise AssertionError("post must not be called when selection itself is refused")


def _image_response(text: str = "hello") -> dict[str, Any]:
    return {
        "errorCode": 0,
        "errorMsg": "Success",
        "result": {
            "dataInfo": {"type": "image", "width": 100.0, "height": 200.0},
            "ocrResults": [{"prunedResult": {"rec_texts": [text], "rec_boxes": [[0, 0, 10, 10]]}}],
        },
    }


def _pdf_response(n_pages: int = 1, text: str = "paddle-line") -> dict[str, Any]:
    return {
        "errorCode": 0,
        "errorMsg": "Success",
        "result": {
            "dataInfo": {
                "type": "pdf",
                "pages": [{"width": 100.0, "height": 200.0} for _ in range(n_pages)],
            },
            "ocrResults": [
                {"prunedResult": {"rec_texts": [text], "rec_boxes": [[0, 0, 10, 10]]}}
                for _ in range(n_pages)
            ],
        },
    }


def _registry(*parsers: Any, priority: tuple[str, ...] | None = None) -> ParserRegistry:
    registry = ParserRegistry(priority)
    for parser in parsers:
        registry.register(parser)
    return registry


class TestNamedParserIsUsed:
    def test_named_parser_is_used(self) -> None:
        def fake_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
            return _image_response()

        paddle = PaddleXParser(_endpoint(), post=fake_post)
        registry = _registry(PdfTextParser(), paddle)

        document = parse(IMAGE, registry=registry, parser="paddle")

        assert document.provenance.parser_id == "paddle"
        assert "hello" in document.text


class TestRefusals:
    def test_unknown_name_refused_listing_known(self) -> None:
        registry = _registry(PdfTextParser())

        with pytest.raises(ParserCapabilityError) as excinfo:
            parse(IMAGE, registry=registry, parser="nope")

        assert "pdf-text" in str(excinfo.value)

    def test_unavailable_name_refused_with_reason(self) -> None:
        registry = _registry(PdfTextParser())
        registry.register(
            PaddleXParser(_endpoint(), post=_refuse_post),
            available=False,
            reason="credentials_not_configured",
        )

        with pytest.raises(ParserCapabilityError) as excinfo:
            parse(IMAGE, registry=registry, parser="paddle")

        assert "credentials_not_configured" in str(excinfo.value)

    def test_named_parser_wrong_media_type_refused_not_fallen_back(self) -> None:
        png_only = PaddleXParser(
            _endpoint(endpoint_id="png-only", media_types=frozenset({PNG})),
            post=_refuse_post,
        )
        registry = _registry(PdfTextParser(), png_only)

        # A PDF, not a PNG -- pdf-text could serve this, but naming an
        # endpoint that cannot never falls back to one that can (ADR-0021 §1).
        with pytest.raises(ParserCapabilityError):
            parse(INVOICE, registry=registry, parser="png-only")


class TestOverride:
    def test_named_recognition_parser_on_text_pdf_is_honoured(self) -> None:
        def fake_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
            return _pdf_response(1)

        paddle = PaddleXParser(_endpoint(), post=fake_post)
        registry = _registry(PdfTextParser(), paddle)

        document = parse(INVOICE, registry=registry, parser="paddle")

        assert document.provenance.parser_id == "paddle"
        assert document.provenance.text_layer is not None
        assert document.provenance.text_layer.overridden is True
        assert document.provenance.text_layer.pages, "the rule's own evidence survives the override"


class TestUnnamedSelectionUnchanged:
    def test_unnamed_selection_unchanged(self) -> None:
        paddle = PaddleXParser(_endpoint(), post=_refuse_post)
        registry = _registry(PdfTextParser(), paddle)

        document = parse(INVOICE, registry=registry)

        assert document.provenance.parser_id == "pdf-text"


class TestDefaultRegistry:
    def test_default_registry_reads_endpoints_file(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        config = tmp_path / "endpoints.json"
        config.write_text(
            json.dumps(
                {
                    "endpoints": [
                        {"id": "paddle", "url": "http://paddle.example/ocr", "version": "1.0.0"}
                    ]
                }
            )
        )
        monkeypatch.setenv(ENDPOINTS_FILE_ENV, str(config))

        registry = default_registry()
        (entry,) = [e for e in registry.candidates_all() if e.id == "paddle"]

        assert entry.available is True
        assert entry.parser is not None
        assert entry.capabilities.requires_network is True

    def test_default_registry_without_env_is_unchanged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv(ENDPOINTS_FILE_ENV, raising=False)

        ids = {entry.id for entry in default_registry().candidates_all()}

        assert ids == {"pdf-text", "azure-di", "gcv"}


class TestNamedParserChangesIdentity:
    def test_named_parser_changes_document_id(self) -> None:
        paddle = PaddleXParser(_endpoint(endpoint_id="paddle"), post=_refuse_post)
        tesseract = PaddleXParser(_endpoint(endpoint_id="tesseract"), post=_refuse_post)
        registry = _registry(PdfTextParser(), paddle, tesseract)

        plan_paddle = plan_parse(INVOICE, registry=registry, parser="paddle")
        plan_tesseract = plan_parse(INVOICE, registry=registry, parser="tesseract")

        assert plan_paddle.document_id != plan_tesseract.document_id
