"""The contract that matters most for `packaging/ocr/tesseract/`: its
``/ocr`` response must be exactly what
``docdoc.ingest.parsers.paddlex.pages_of``/``build_document`` already read,
because that mapping is what `PaddleXParser` uses for *every* configured
endpoint speaking this protocol -- the official PaddleX serving CLI, this
server, or a third one standing up later (ADR-0021).

The server is loaded fresh, by path, the same way `test_tesseract_server.py`
does -- it is a separate deployable, not part of the `docdoc` distribution
this test otherwise imports. `run_tesseract` is faked, so no `tesseract`
binary is needed to run this.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import sys
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")

import pypdfium2 as pdfium
from fastapi.testclient import TestClient

from docdoc.ingest.capabilities import ParserCapabilities
from docdoc.ingest.parsers.paddlex import build_document, pages_of
from docdoc.ingest.source import JPEG, PDF, PNG, SourceFile
from docdoc.kernel import Document

SERVER_DIR = Path(__file__).resolve().parents[2] / "packaging" / "ocr" / "tesseract" / "server"

#: One recognized word per page -- just enough for `lines_from_tsv` to
#: report a non-empty, scored line; the server's own unit tests already
#: cover the column-split logic in detail.
FAKE_TSV = "\n".join(
    [
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
        "5\t1\t1\t1\t1\t1\t10\t10\t50\t20\t92\tHello",
    ]
)

CAPABILITIES = ParserCapabilities(
    text=True,
    geometry=True,
    tables=False,
    handwriting=False,
    media_types=frozenset({PDF, JPEG, PNG}),
    requires_network=True,
)


def _load(name: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Loaded the same way `test_tesseract_server.py` does -- see that
    module's own docstring on `_load` for why `monkeypatch.setitem` rather
    than assigning `sys.modules` directly.
    """
    spec = importlib.util.spec_from_file_location(name, SERVER_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def _load_app(monkeypatch: pytest.MonkeyPatch) -> Any:
    _load("ocr", monkeypatch)
    return _load("app", monkeypatch)


def _two_page_pdf() -> bytes:
    document = pdfium.PdfDocument.new()
    try:
        document.new_page(200, 300)
        document.new_page(200, 300)
        buffer = io.BytesIO()
        document.save(buffer)
        return buffer.getvalue()
    finally:
        document.close()


def test_tesseract_response_maps_to_a_valid_two_page_document(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MAX_PAGES", raising=False)
    app_module = _load_app(monkeypatch)
    monkeypatch.setattr(app_module.ocr, "run_tesseract", lambda png, langs: FAKE_TSV)

    data = _two_page_pdf()
    client = TestClient(app_module.app)
    response = client.post(
        "/ocr", json={"file": base64.b64encode(data).decode("ascii"), "fileType": 0}
    )
    assert response.status_code == 200
    body = response.json()

    source = SourceFile.from_bytes(data, filename="synthetic.pdf")
    pages = pages_of(body, parser_id="tesseract", blob_id=source.blob_id)
    document = build_document(
        pages,
        source=source,
        options={},
        text_layer=None,
        parser_id="tesseract",
        parser_version="1.0.0+tesseract-test",
        capabilities=CAPABILITIES,
    )

    assert isinstance(document, Document)
    assert len(document.pages) == 2
    # One "Hello" line per page (`FAKE_TSV`'s one word), so exactly two
    # tokens, both carrying the confidence tesseract reported (92 / 100).
    assert len(document.tokens) == 2
    assert document.text.count("Hello") == 2
    assert all(token.source_confidence == pytest.approx(0.92) for token in document.tokens)
