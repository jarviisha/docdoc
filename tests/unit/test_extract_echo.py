"""T035, T036, T039, T040 — User Story 1 end to end, offline.

No credentials, no network, no database, no object storage. If any of these tests
needs one of those, the milestone's central claim about contributor reach is
false (FR-044, SC-001).
"""

from __future__ import annotations

import copy
from decimal import Decimal
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
from docdoc.extraction.value import ExtractedValue
from tests.support import make_document

DOCUMENT_TEXT = "ACME LTD\nINV-001\n1 March 2026\nWidget, large 1,000.00\nTotal 1,240.00\n"


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths(["schemas"])


@pytest.fixture
def echo() -> EchoAdapter:
    return EchoAdapter.from_fixtures("tests/fixtures/echo")


def _extract(registry: SchemaRegistry, echo: EchoAdapter, identity: str = "invoice@1") -> Any:
    return extract(
        make_document(DOCUMENT_TEXT), schema=identity, registry=registry, adapter=echo
    )


def test_every_declared_field_appears(registry: SchemaRegistry, echo: EchoAdapter) -> None:
    """SC-002 -- including the ones the document does not contain."""
    result = _extract(registry, echo)
    assert set(result.values) == {f.name for f in registry.resolve("invoice@1").schema.fields}
    assert result.discarded == ()


def test_values_are_typed_not_strings(registry: SchemaRegistry, echo: EchoAdapter) -> None:
    """A total that has been through a float is not the total that was printed."""
    result = _extract(registry, echo)
    assert result.value_at("total").value == Decimal("1240.00")
    assert isinstance(result.value_at("total").value, Decimal)
    assert result.value_at("issue_date").value.isoformat() == "2026-03-01"


def test_claimed_text_is_byte_faithful(registry: SchemaRegistry, echo: EchoAdapter) -> None:
    """EXT-18, SC-003 -- Milestone 4 cannot locate text this layer altered."""
    result = _extract(registry, echo)
    assert result.value_at("total").claimed_text == "1,240.00"
    assert result.value_at("issue_date").claimed_text == "1 March 2026"
    assert result.value_at("supplier.legal_name").claimed_text == "ACME LTD"


def test_absence_is_explicit_not_missing(registry: SchemaRegistry, echo: EchoAdapter) -> None:
    """EXT-16, FR-005 -- an absent field is a recorded outcome, not a gap."""
    result = _extract(registry, echo)
    due = result.value_at("due_date")
    assert due.present is False
    assert due.value is None
    assert due.claimed_text is None


def test_repeating_group_returns_one_entry_per_occurrence(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    result = _extract(registry, echo)
    items = result.values["line_items"]
    assert len(items) == 2
    assert items[0]["description"].value == "Widget, large"
    assert items[1]["amount"].value == Decimal("240.00")
    assert items[0]["amount"].claimed_text == "1,000.00"


def test_model_confidence_is_carried_and_untrusted(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """FR-031, ADR-0004 -- stored verbatim, routing nothing."""
    result = _extract(registry, echo)
    assert result.value_at("total").model_confidence == 0.91
    assert result.value_at("issue_date").model_confidence is None


def test_a_second_document_type_needs_no_engine_change(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """SC-014 -- receipt@1 is data, and the same call extracts it."""
    result = _extract(registry, echo, identity="receipt@1")
    assert result.provenance.schema_identity == "receipt@1"
    assert result.value_at("merchant_name").value == "Corner Store"
    assert result.value_at("total").value == Decimal("12.50")


# -- the refusal to repair ---------------------------------------------------


def _payload(**overrides: Any) -> dict[str, Any]:
    import json
    import pathlib

    base = json.loads(pathlib.Path("tests/fixtures/echo/invoice@1.json").read_text())
    base.update(overrides)
    return base


def test_a_wrong_shape_is_an_error_not_a_coercion(registry: SchemaRegistry) -> None:
    with pytest.raises(ExtractionError) as caught:
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=EchoAdapter.malformed(),
        )
    assert caught.value.reason == "missing_field"
    assert caught.value.field_path
    assert caught.value.schema_identity == "invoice@1"
    assert caught.value.adapter_id == "echo"


def test_an_unparseable_value_names_the_field(registry: SchemaRegistry) -> None:
    payload = _payload(total={"value": 1240.0, "claimed_text": "1,240.00", "confidence": None})
    with pytest.raises(ExtractionError) as caught:
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=EchoAdapter.returning("invoice@1", payload),
        )
    assert caught.value.reason == "type"
    assert caught.value.field_path == "total"


def test_a_scalar_where_a_repeating_group_was_asked_for(registry: SchemaRegistry) -> None:
    payload = _payload(line_items={"value": "nope", "claimed_text": None, "confidence": None})
    with pytest.raises(ExtractionError) as caught:
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=EchoAdapter.returning("invoice@1", payload),
        )
    assert caught.value.reason == "shape"
    assert caught.value.field_path == "line_items"


def test_an_undeclared_field_is_discarded_and_recorded(registry: SchemaRegistry) -> None:
    """FR-008 -- never merged into the result, never silently dropped either."""
    payload = _payload(bogus={"value": "x", "claimed_text": "x", "confidence": None})
    result = extract(
        make_document(DOCUMENT_TEXT),
        schema="invoice@1",
        registry=registry,
        adapter=EchoAdapter.returning("invoice@1", payload),
    )
    assert "bogus" not in result.values
    assert result.discarded == ("bogus",)


def test_a_refusal_is_not_an_answer(registry: SchemaRegistry) -> None:
    """research.md R12 -- it arrives as a *successful* response on the wire."""
    with pytest.raises(ModelProviderError) as caught:
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=EchoAdapter.refusing(category="cyber"),
        )
    assert caught.value.transient is False
    assert caught.value.refusal_category == "cyber"
    assert caught.value.adapter_id == "echo"


# -- non-mutation, on every path ---------------------------------------------


def _snapshot(document: Any) -> tuple[Any, ...]:
    return (document.id, document.text, document.provenance, len(document.tokens))


@pytest.mark.parametrize(
    "adapter_factory",
    [
        EchoAdapter.malformed,
        EchoAdapter.refusing,
        lambda: EchoAdapter.failing(reason="service"),
        lambda: EchoAdapter.failing(reason="timeout"),
    ],
    ids=["malformed", "refusal", "provider-error", "timeout"],
)
def test_the_document_is_never_corrupted(
    registry: SchemaRegistry, adapter_factory: Any
) -> None:
    """T039 and Principle XII.

    "Provider failure never corrupts the canonical document" is one of the
    invariants the constitution lists as MUST-be-tested. The success path is the
    easy half; the failure paths are the half that matters, because that is where
    a partially-built result would be tempted to write back.
    """
    document = make_document(DOCUMENT_TEXT)
    before = _snapshot(document)
    with pytest.raises((ExtractionError, ModelProviderError)):
        extract(document, schema="invoice@1", registry=registry, adapter=adapter_factory())
    assert _snapshot(document) == before


def test_the_document_is_unchanged_by_a_successful_extraction(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    document = make_document(DOCUMENT_TEXT)
    before = _snapshot(document)
    extract(document, schema="invoice@1", registry=registry, adapter=echo)
    assert _snapshot(document) == before


def test_an_over_budget_document_is_refused_before_anything_is_sent(
    registry: SchemaRegistry,
) -> None:
    """FR-030, FR-046 -- and the error names the way forward."""

    class RecordingAdapter(EchoAdapter):
        calls = 0

        def complete(self, request: Any, options: Any) -> Any:  # type: ignore[override]
            RecordingAdapter.calls += 1
            return super().complete(request, options)

    adapter = RecordingAdapter.from_fixtures("tests/fixtures/echo")
    with pytest.raises(ExtractionError) as caught:
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@1",
            registry=registry,
            adapter=adapter,
            options=ExtractionOptions(input_budget_tokens=5),
        )
    assert caught.value.reason == "input_budget"
    assert "Document.slice" in str(caught.value)
    assert RecordingAdapter.calls == 0, "the guard must run before any transmission"


def test_values_are_extracted_values_all_the_way_down(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    result = _extract(registry, echo)
    assert isinstance(result.values["total"], ExtractedValue)
    assert isinstance(result.values["supplier"], dict)
    assert isinstance(result.values["line_items"], tuple)
    assert isinstance(result.values["line_items"][0]["amount"], ExtractedValue)


def test_copies_of_the_result_do_not_share_mutable_state(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    result = _extract(registry, echo)
    assert copy.deepcopy(result.values)["total"].value == result.value_at("total").value
