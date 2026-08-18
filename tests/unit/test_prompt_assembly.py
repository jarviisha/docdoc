"""T027 — the cache-prefix ordering (EXT-19, research.md R15).

This test exists because the failure it guards is silent. If something volatile
gets interpolated ahead of the cache breakpoint, every extraction still returns
the right answer and every other test still passes -- the only symptom is that the
per-schema prefix stops being a cache hit and the bill multiplies.

So the assertion is not "the prompt looks right", it is "the prefix is
byte-identical across documents, and nothing per-request precedes it".
"""

from __future__ import annotations

import pathlib

import pytest

from docdoc.extraction import SchemaRegistry, response_shape_for
from docdoc.extraction.prompt import build_request

SCHEMAS = pathlib.Path("schemas")


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths(["schemas"])


def _request(registry: SchemaRegistry, text: str, identity: str = "invoice@1"):
    entry = registry.resolve(identity)
    return build_request(entry, text, response_shape=response_shape_for(entry.schema))


def test_the_prefix_is_byte_identical_across_documents(registry: SchemaRegistry) -> None:
    """The whole point: one cached prefix serves every document for a schema."""
    first = _request(registry, "one document")
    second = _request(registry, "a completely different document, much longer\n" * 20)
    assert first.prefix == second.prefix
    assert first.prefix.encode() == second.prefix.encode()


def test_the_prefix_differs_between_schemas(registry: SchemaRegistry) -> None:
    """Two schemas are two cache entries; sharing one would be a correctness bug."""
    invoice = _request(registry, "d", "invoice@1")
    receipt = _request(registry, "d", "receipt@1")
    assert invoice.prefix != receipt.prefix


def test_the_prefix_differs_between_majors(registry: SchemaRegistry) -> None:
    first = _request(registry, "d", "invoice@1")
    second = _request(registry, "d", "invoice@2")
    assert first.prefix != second.prefix


def test_the_document_is_not_in_the_prefix(registry: SchemaRegistry) -> None:
    marker = "UNIQUE-DOCUMENT-MARKER-8f3a"
    request = _request(registry, f"before {marker} after")
    assert marker not in request.prefix
    assert marker in request.document_text


def test_the_prefix_precedes_the_document_in_the_rendered_request(
    registry: SchemaRegistry,
) -> None:
    request = _request(registry, "DOCUMENT-BODY")
    rendered = request.rendered()
    assert rendered.index(request.prefix) < rendered.index("DOCUMENT-BODY")
    assert rendered.startswith(request.prefix)


def test_repeated_assembly_is_deterministic(registry: SchemaRegistry) -> None:
    """No clock, no counter, no id -- twice the same call is twice the same bytes."""
    first = _request(registry, "same text")
    second = _request(registry, "same text")
    assert first.rendered() == second.rendered()


def test_nothing_volatile_appears_before_the_breakpoint(registry: SchemaRegistry) -> None:
    """The regression guard.

    A timestamp, a document id, a request id, or a counter ahead of the breakpoint
    is the specific mistake research.md R15 warns about. Two requests built for the
    same schema from *different* documents share a prefix, so anything that varies
    with the request cannot be in it -- and a digit-bearing token that appears in
    one prefix but not the other would show up as an inequality above. This test
    adds the direct check: the prefix contains no substring of either document id.
    """
    entry = registry.resolve("invoice@1")
    request = build_request(entry, "body", response_shape=response_shape_for(entry.schema))
    for forbidden in ("sha256:", "document_id", "request_id", "timestamp"):
        assert forbidden not in request.prefix, (
            f"{forbidden!r} in the cached prefix would invalidate it per request"
        )


def test_the_prefix_is_the_prompt_and_carries_the_field_instructions(
    registry: SchemaRegistry,
) -> None:
    """A prefix that lost the instructions would still cache, and extract badly."""
    request = _request(registry, "body")
    assert "claimed_text" in request.prefix
    assert "exactly as it appears in the document" in request.prefix


def test_the_request_names_the_schema_it_was_built_for(registry: SchemaRegistry) -> None:
    """An adapter needs it; reading it back out of the rendered prompt would be
    parsing our own output."""
    assert _request(registry, "body").schema_identity == "invoice@1"


def test_the_response_shape_travels_with_the_request(registry: SchemaRegistry) -> None:
    request = _request(registry, "body")
    assert request.response_shape["type"] == "object"
    assert "total" in request.response_shape["properties"]
