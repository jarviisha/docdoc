"""The live PaddleX-protocol services, behind the ``provider`` marker.

Deselected by default and skipped with a stated reason when neither URL is
configured, so the unit, property, and contract suites remain meaningful for
a contributor who has no tunnel open (FR-034, SC-009). Two services, two
independent skip conditions, because a contributor may have one tunnel open
and not the other.

What this proves is narrow but not replaceable: that the wire still works
end to end against each real deployment -- the request shape, a real
response, and that it still converts into a Document that locates its own
text. The *mapping* is pinned offline by Task 2's recorded fixtures in
tests/unit/test_paddlex_mapping.py; the *HTTP* half is pinned offline by
tests/unit/test_paddlex_parser.py. Neither can prove a live service still
speaks the protocol the way those fixtures recorded it in 2026-10-19 --
that is this file's whole job.

    DOCDOC_TEST_PADDLEX_URL=http://127.0.0.1:18080 \\
    DOCDOC_TEST_TESSERACT_URL=http://127.0.0.1:18081 \\
      uv run pytest -m provider tests/integration/test_paddlex_live.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("pypdfium2")

from docdoc.ingest.options import TransportSettings
from docdoc.ingest.parsers.paddlex import OcrEndpoint, PaddleXParser
from docdoc.ingest.source import SourceFile

PADDLEX_URL_ENV = "DOCDOC_TEST_PADDLEX_URL"
TESSERACT_URL_ENV = "DOCDOC_TEST_TESSERACT_URL"

pytestmark = pytest.mark.provider

FIXTURES = Path(__file__).parent.parent / "fixtures"

# CPU OCR on the tunneled host is slow -- research.md and record.py both
# measured up to ~180s for a single request -- so this budget is a live
# service's, not a unit test's (record.py's own REQUEST_TIMEOUT_S = 180).
LIVE = TransportSettings(max_attempts=2, attempt_timeout_s=200.0, deadline_s=400.0)


@pytest.fixture
def invoice() -> SourceFile:
    path = FIXTURES / "pdf" / "digital_invoice.pdf"
    return SourceFile.from_bytes(path.read_bytes(), filename=path.name)


@pytest.mark.skipif(
    not os.environ.get(PADDLEX_URL_ENV),
    reason=f"live service test: set {PADDLEX_URL_ENV} to run it",
)
def test_the_paddlex_endpoint_recognizes_the_invoice(invoice: SourceFile) -> None:
    endpoint = OcrEndpoint(
        id="paddle-live", url=f"{os.environ[PADDLEX_URL_ENV]}/ocr", version="live"
    )
    document = PaddleXParser(endpoint).parse(invoice, {}, LIVE)

    assert "INV-001" in document.text


@pytest.mark.skipif(
    not os.environ.get(TESSERACT_URL_ENV),
    reason=f"live service test: set {TESSERACT_URL_ENV} to run it",
)
def test_the_tesseract_shim_endpoint_recognizes_the_invoice(invoice: SourceFile) -> None:
    endpoint = OcrEndpoint(
        id="tesseract-live", url=f"{os.environ[TESSERACT_URL_ENV]}/ocr", version="live"
    )
    document = PaddleXParser(endpoint).parse(invoice, {}, LIVE)

    assert "INV-001" in document.text
