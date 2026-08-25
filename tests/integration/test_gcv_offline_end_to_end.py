"""A PNG through the whole ingest layer to the Vision parser, with no network.

The mapping tests call ``map_annotate_result`` directly and the availability
tests never parse, so the plumbing between them -- routing an image, selecting by
capability, running the parser, and holding it to what it declared -- was covered
for the Azure path only. This runs it for the second recognition parser using the
adapter's injectable ``annotate``, so a wiring mistake fails here rather than in a
credentialed environment.

Nothing here names a provider to *choose* one: the request asks for text and
geometry on a PNG, and the registry supplies the parser.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from docdoc.ingest import CapabilityRequest
from docdoc.ingest.parse import execute_plan, plan_parse
from docdoc.ingest.parsers.gcv import GoogleCloudVisionParser
from docdoc.ingest.registry import ParserRegistry
from docdoc.ingest.source import PNG

FIXTURES = Path(__file__).parent.parent / "fixtures"
RECORDED = FIXTURES / "gcv" / "sample_page.annotate.json"
IMAGE = FIXTURES / "image" / "sample_page.png"

IMAGE_REQUEST = CapabilityRequest(media_type=PNG, text=True, geometry=True)


@pytest.fixture
def registry() -> ParserRegistry:
    """A registry holding only the Vision parser, answering from the recording.

    Built directly rather than via ``default_registry`` so the test does not
    depend on which extras the environment happens to have, and so the recorded
    response is what the parser returns.
    """
    recorded = json.loads(RECORDED.read_text())

    def annotate(source: Any, transport: Any, deadline: Any) -> Any:
        return recorded

    registry = ParserRegistry()
    registry.register(GoogleCloudVisionParser(annotate=annotate))
    return registry


def test_an_image_parses_end_to_end(registry: ParserRegistry) -> None:
    plan = plan_parse(IMAGE.read_bytes(), require=IMAGE_REQUEST, registry=registry)
    document = execute_plan(plan)

    assert document.text == "Invoice INV-001\nTotal 228.00\n"
    assert len(document.pages) == 1


def test_the_routing_verdict_says_an_image_has_no_text_layer(
    registry: ParserRegistry,
) -> None:
    # An image is short-circuited without inspecting its bytes (ING-13), and the
    # verdict is recorded rather than inferred later.
    plan = plan_parse(IMAGE.read_bytes(), require=IMAGE_REQUEST, registry=registry)

    assert plan.verdict.text_layer_usable is False
    assert execute_plan(plan).provenance.text_layer_used is False


def test_the_document_id_is_available_before_the_service_is_called() -> None:
    """FR-059: identity is computable without paying for a parse.

    The stub would answer instantly, so this asserts the stronger thing -- that
    planning never calls the parser at all -- by giving it one that raises.
    """

    def explode(source: Any, transport: Any, deadline: Any) -> Any:
        raise AssertionError("planning must not reach the service")

    registry = ParserRegistry()
    registry.register(GoogleCloudVisionParser(annotate=explode))

    plan = plan_parse(IMAGE.read_bytes(), require=IMAGE_REQUEST, registry=registry)

    assert plan.document_id
    assert plan.parser.id == "gcv"


def test_a_value_locates_to_a_box_on_the_page(registry: ParserRegistry) -> None:
    document = execute_plan(
        plan_parse(IMAGE.read_bytes(), require=IMAGE_REQUEST, registry=registry)
    )

    (span,) = document.find("228.00")
    (geometry,) = document.locate(span)

    assert geometry.page_index == 0
    assert 0.0 <= geometry.bbox.y0 < geometry.bbox.y1 <= 1.0


def test_provenance_names_the_parser_that_ran(registry: ParserRegistry) -> None:
    document = execute_plan(
        plan_parse(IMAGE.read_bytes(), require=IMAGE_REQUEST, registry=registry)
    )

    assert document.provenance.parser_id == "gcv"
    assert document.provenance.parser_version.startswith("1.0.0+gcv-")
    assert document.provenance.reading_order == "gcv-block-order@1"


def test_asking_for_tables_is_refused_rather_than_served_without_them(
    registry: ParserRegistry,
) -> None:
    """The declaration is what keeps a caller from getting a silent downgrade.

    This parser reads words, not cell structure. A caller who needs tables must be
    told no, not handed a document whose empty ``tables`` looks like a document
    that had none.
    """
    from docdoc.ingest.errors import ParserCapabilityError

    with pytest.raises(ParserCapabilityError) as caught:
        plan_parse(
            IMAGE.read_bytes(),
            require=CapabilityRequest(media_type=PNG, text=True, tables=True),
            registry=registry,
        )

    assert "tables" in caught.value.required
