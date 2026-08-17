"""T081 — re-extraction produces a new result, never a rewritten one (FR-038, FR-043).

Principle VIII: "Provenance MUST NOT be silently overwritten. Reprocessing produces
a new result with new provenance; it does not mutate the prior one."

That is easy to satisfy today, when nothing is stored, and easy to lose the moment
something is. These tests are the record of what re-extraction is supposed to mean,
placed before there is a cache to get it wrong.
"""

from __future__ import annotations

from typing import Any

import pytest

from docdoc.extraction import (
    ExtractionError,
    ExtractionOptions,
    ModelProviderError,
    SchemaRegistry,
    extract,
)
from docdoc.extraction.adapters.echo import EchoAdapter
from tests.support import make_document

DOCUMENT_TEXT = "ACME LTD\nINV-001\nPO-77\nTotal 1,240.00\n"


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths(["schemas"])


@pytest.fixture
def echo() -> EchoAdapter:
    return EchoAdapter.from_fixtures("tests/fixtures/echo")


def _snapshot(result: Any) -> tuple[Any, ...]:
    """Everything about a result that must survive a later extraction."""
    return (
        result.artifact_id,
        result.provenance.model_dump(),
        result.discarded,
        tuple(sorted((k, str(v)) for k, v in result.values.items())),
    )


def test_extracting_twice_yields_two_independent_results(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    document = make_document(DOCUMENT_TEXT)
    first = extract(document, schema="invoice@1", registry=registry, adapter=echo)
    before = _snapshot(first)

    second = extract(document, schema="invoice@1", registry=registry, adapter=echo)

    assert second is not first
    assert _snapshot(first) == before, "the earlier result is untouched"
    # Identical inputs, so the artifact ids agree -- that is the cache key working,
    # not the two results being the same object.
    assert second.artifact_id == first.artifact_id
    assert second.provenance is not first.provenance


def test_re_extracting_under_a_newer_major_leaves_the_older_result_intact(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """The case Principle VIII is written for."""
    document = make_document(DOCUMENT_TEXT)
    old = extract(document, schema="invoice@1", registry=registry, adapter=echo)
    before = _snapshot(old)

    new = extract(document, schema="invoice@2", registry=registry, adapter=echo)

    assert new.artifact_id != old.artifact_id
    assert new.provenance.schema_identity == "invoice@2"
    assert old.provenance.schema_identity == "invoice@1"
    assert _snapshot(old) == before


def test_a_failed_re_extraction_does_not_disturb_the_earlier_result(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """FR-043 -- a failure leaves no partial result and mutates no existing one."""
    document = make_document(DOCUMENT_TEXT)
    good = extract(document, schema="invoice@1", registry=registry, adapter=echo)
    before = _snapshot(good)

    for adapter in (
        EchoAdapter.malformed(),
        EchoAdapter.refusing(),
        EchoAdapter.failing(reason="timeout"),
    ):
        with pytest.raises((ExtractionError, ModelProviderError)):
            extract(document, schema="invoice@1", registry=registry, adapter=adapter)
        assert _snapshot(good) == before

    with pytest.raises(ExtractionError):
        extract(
            document,
            schema="invoice@1",
            registry=registry,
            adapter=echo,
            options=ExtractionOptions(input_budget_tokens=5),
        )
    assert _snapshot(good) == before


def test_results_are_frozen(registry: SchemaRegistry, echo: EchoAdapter) -> None:
    """The structural half: a result cannot be rewritten even by mistake."""
    result = extract(
        make_document(DOCUMENT_TEXT), schema="invoice@1", registry=registry, adapter=echo
    )
    with pytest.raises(Exception, match=r"frozen|immutable"):
        result.artifact_id = "sha256:something-else"  # type: ignore[misc]
    with pytest.raises(Exception, match=r"frozen|immutable"):
        result.provenance.schema_hash = "sha256:x"  # type: ignore[misc]


def test_extracted_values_are_frozen(registry: SchemaRegistry, echo: EchoAdapter) -> None:
    result = extract(
        make_document(DOCUMENT_TEXT), schema="invoice@1", registry=registry, adapter=echo
    )
    with pytest.raises(Exception, match=r"frozen|immutable"):
        result.value_at("total").value = 0  # type: ignore[misc]


def test_two_results_from_one_document_do_not_share_value_objects(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """Sharing would make the freeze the only thing standing between them."""
    document = make_document(DOCUMENT_TEXT)
    first = extract(document, schema="invoice@1", registry=registry, adapter=echo)
    second = extract(document, schema="invoice@1", registry=registry, adapter=echo)
    assert first.values["total"] is not second.values["total"]
    assert first.values["line_items"] is not second.values["line_items"]


def test_extraction_does_not_mutate_the_registry(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """An extraction that changed the registry would change later extractions."""
    before = (registry.identities(), registry.resolve("invoice@1").schema_hash)
    extract(make_document(DOCUMENT_TEXT), schema="invoice@1", registry=registry, adapter=echo)
    assert (registry.identities(), registry.resolve("invoice@1").schema_hash) == before
