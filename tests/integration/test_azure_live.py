"""T044 — the live service, behind the ``provider`` marker.

Deselected by default and skipped with a stated reason when credentials are
absent, so the unit, property, and contract suites remain meaningful for a
contributor who has none (FR-034, SC-009).

What this proves is narrow but not replaceable: that the wire still works --
authentication, the long-running-operation poll, and the shape of a real
response. The *mapping* is pinned offline by the recorded responses in
tests/unit/test_azure_mapping.py, which is where a regression would actually be
caught (research.md R14).

    uv run pytest -m provider
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from docdoc.ingest import CapabilityRequest, parse
from docdoc.ingest.options import TransportSettings
from docdoc.ingest.parsers.azure_di import (
    ENDPOINT_ENV,
    KEY_ENV,
    AzureDocumentIntelligenceParser,
    credentials_available,
)
from docdoc.ingest.source import SourceFile
from docdoc.ingest.validate import validate_output

pytestmark = [
    pytest.mark.provider,
    pytest.mark.skipif(
        not credentials_available(),
        reason=f"live service test: set {ENDPOINT_ENV} and {KEY_ENV} to run it",
    ),
]

FIXTURES = Path(__file__).parent.parent / "fixtures"

# Generous: a real service call is not a unit test, and a flaky timeout here
# would say nothing about docdoc.
LIVE = TransportSettings(max_attempts=3, attempt_timeout_s=120.0, deadline_s=300.0)


@pytest.fixture
def scanned() -> SourceFile:
    path = FIXTURES / "pdf" / "scanned_contract.pdf"
    return SourceFile.from_bytes(path.read_bytes(), filename=path.name)


def test_a_scanned_pdf_becomes_a_valid_document(scanned: SourceFile) -> None:
    parser = AzureDocumentIntelligenceParser()

    document = parser.parse(scanned, {}, LIVE)

    validate_output(document, parser.capabilities, parser_id=parser.id, blob_id=scanned.blob_id)
    assert document.pages
    assert list(document.tokens), "a scan of a text document should recognize something"


def test_geometry_comes_back_normalized(scanned: SourceFile) -> None:
    document = AzureDocumentIntelligenceParser().parse(scanned, {}, LIVE)

    for token in document.tokens:
        assert token.geometry is not None
        for value in token.geometry.bbox:
            assert 0.0 <= value <= 1.0


def test_no_service_type_survives_into_the_document(scanned: SourceFile) -> None:
    document = AzureDocumentIntelligenceParser().parse(scanned, {}, LIVE)
    rendered = repr(document)

    for leaked in ("polygon", "boundingRegions", "pageNumber", "AnalyzeResult"):
        assert leaked not in rendered


def test_the_document_is_indistinguishable_in_shape_from_a_native_one(
    scanned: SourceFile,
) -> None:
    """The promise US3 makes: downstream code cannot tell which path produced a
    document, except by reading provenance."""
    native = parse(
        (FIXTURES / "pdf" / "digital_invoice.pdf").read_bytes(),
        require=CapabilityRequest(media_type="application/pdf", geometry=True),
    )
    recognized = AzureDocumentIntelligenceParser().parse(scanned, {}, LIVE)

    assert type(native) is type(recognized)
    for document in (native, recognized):
        assert document.pages
        assert document.source.blob_id.startswith("sha256:")
        assert document.provenance.reading_order

    assert native.provenance.text_layer_used is True
    assert recognized.provenance.text_layer_used is False


def test_an_image_is_accepted_too() -> None:
    path = FIXTURES / "image" / "sample_page.png"
    source = SourceFile.from_bytes(path.read_bytes(), filename=path.name)

    document = AzureDocumentIntelligenceParser().parse(source, {}, LIVE)

    assert len(document.pages) == 1


def test_a_rejected_credential_fails_fast(scanned: SourceFile) -> None:
    """One live negative case, because auth is the failure most likely to be
    mis-mapped and the only one a recorded response cannot rehearse."""
    from docdoc.ingest.errors import ProviderError

    previous = os.environ[KEY_ENV]
    os.environ[KEY_ENV] = "0" * 32
    try:
        with pytest.raises(ProviderError) as caught:
            AzureDocumentIntelligenceParser().parse(
                scanned, {}, TransportSettings(max_attempts=3, deadline_s=60.0)
            )
    finally:
        os.environ[KEY_ENV] = previous

    assert caught.value.reason == "auth"
    assert caught.value.attempts == 1, "a rejected credential must not be retried"
