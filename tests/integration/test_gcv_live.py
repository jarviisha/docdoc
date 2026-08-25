"""The live Cloud Vision service, behind the ``provider`` marker.

Deselected by default and skipped with a stated reason when credentials are
absent, so the unit, property, and contract suites remain meaningful for a
contributor who has none (FR-034, SC-009).

What this proves is narrow but not replaceable: that the wire still works --
authentication, the request shape, and that a real response still converts into
the REST JSON the mapping expects. That last one is the reason this file earns
its keep: ``to_dict`` is where the SDK's proto field names and integer enums are
turned into the camelCase-and-names shape ``map_annotate_result`` reads, and no
offline test can prove the SDK still honours those arguments. The *mapping* is
pinned offline by the recorded response in tests/unit/test_gcv_mapping.py, which
is where a regression would actually be caught (research.md R14).

    uv run pytest -m provider
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docdoc.ingest import CapabilityRequest, parse
from docdoc.ingest.options import TransportSettings
from docdoc.ingest.parsers.gcv import (
    CREDENTIALS_ENV,
    GOOGLE_CREDENTIALS_ENV,
    GoogleCloudVisionParser,
    credentials_available,
)
from docdoc.ingest.source import PNG, SourceFile
from docdoc.ingest.validate import validate_output

pytest.importorskip("google.cloud.vision")

pytestmark = [
    pytest.mark.provider,
    pytest.mark.skipif(
        not credentials_available(),
        reason=(
            f"live service test: set {GOOGLE_CREDENTIALS_ENV} "
            f"(or {CREDENTIALS_ENV} for another credential source) to run it"
        ),
    ),
]

FIXTURES = Path(__file__).parent.parent / "fixtures"

# Generous: a real service call is not a unit test, and a flaky timeout here
# would say nothing about docdoc.
LIVE = TransportSettings(max_attempts=3, attempt_timeout_s=120.0, deadline_s=300.0)


@pytest.fixture
def image() -> SourceFile:
    path = FIXTURES / "image" / "sample_page.png"
    return SourceFile.from_bytes(path.read_bytes(), filename=path.name)


def test_an_image_becomes_a_valid_document(image: SourceFile) -> None:
    parser = GoogleCloudVisionParser()

    document = parser.parse(image, {}, LIVE)

    validate_output(
        document,
        parser.capabilities,
        parser_id=parser.id,
        blob_id=image.blob_id,
        parser_version=parser.version,
    )
    assert len(document.pages) == 1


def test_the_recognized_text_is_locatable(image: SourceFile) -> None:
    document = GoogleCloudVisionParser().parse(image, {}, LIVE)

    spans = document.find("INV-001")
    assert spans, f"the service read no invoice number from the fixture: {document.text!r}"
    (geometry,) = document.locate(spans[0])
    assert geometry.page_index == 0
    assert 0.0 <= geometry.bbox.x0 <= 1.0


def test_the_sdk_response_still_converts_to_the_shape_the_mapping_reads(
    image: SourceFile,
) -> None:
    """The one thing no offline test can cover.

    ``_annotate_over_the_wire`` asks ``to_dict`` for camelCase keys and named
    enums. If a future SDK drops or renames either argument, the mapping would
    receive snake_case keys and integer enums, find nothing it recognizes, and
    fail as an empty result -- a confusing way to learn about an SDK change.
    """
    parser = GoogleCloudVisionParser()
    result = parser._annotate_over_the_wire(image, LIVE, LIVE.start())

    annotation = result["fullTextAnnotation"]
    page = annotation["pages"][0]
    word = page["blocks"][0]["paragraphs"][0]["words"][0]

    assert "boundingBox" in word, f"keys are not camelCase: {sorted(word)}"
    break_type = (word["symbols"][-1].get("property") or {}).get("detectedBreak", {}).get("type")
    if break_type is not None:
        assert isinstance(break_type, str), "enums came back as integers, not names"


def test_selection_reaches_it_without_naming_it(image: SourceFile) -> None:
    """A caller asks for image OCR by capability; configuration supplies the parser."""
    document = parse(
        image.data,
        require=CapabilityRequest(media_type=PNG, text=True, geometry=True),
        transport=LIVE,
    )

    assert document.provenance.text_layer_used is False
    assert document.provenance.parser_id in {"gcv", "azure-di"}
