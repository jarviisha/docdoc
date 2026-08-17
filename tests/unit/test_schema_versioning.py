"""T042, T043 — User Story 2: a schema version that means something.

A developer pins to ``invoice@1``. Their stored results say ``invoice@1`` forever,
and the number moves only when the contract they depend on actually breaks -- not
because a colleague reworded a field description.

These tests are what stop the two identities of ADR-0008 collapsing back into one.
"""

from __future__ import annotations

import pytest

from docdoc.extraction import (
    ExtractionOptions,
    SchemaError,
    SchemaRegistry,
    extract,
    schema_hash_for,
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


def _extract(registry: SchemaRegistry, echo: EchoAdapter, identity: str):
    return extract(make_document(DOCUMENT_TEXT), schema=identity, registry=registry, adapter=echo)


# -- concurrent majors -------------------------------------------------------


def test_two_majors_extract_independently(registry: SchemaRegistry, echo: EchoAdapter) -> None:
    """EXT-11, SC-008 -- neither shadows the other, and both are usable at once."""
    v1 = _extract(registry, echo, "invoice@1")
    v2 = _extract(registry, echo, "invoice@2")

    assert v1.provenance.schema_identity == "invoice@1"
    assert v2.provenance.schema_identity == "invoice@2"
    assert "purchase_order_number" not in v1.values
    assert v2.values["purchase_order_number"].value == "PO-77"


def test_each_result_names_the_exact_identity_and_hash(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """SC-004 -- readable without re-running the extraction."""
    result = _extract(registry, echo, "invoice@1")
    entry = registry.resolve("invoice@1")
    assert result.provenance.schema_identity == entry.identity
    assert result.provenance.schema_hash == entry.schema_hash
    assert result.provenance.schema_hash == schema_hash_for(entry.schema)
    assert result.provenance.prompt_hash == entry.prompt_hash


def test_the_two_majors_get_different_artifact_ids(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """A stored result must not be mistakable for one produced under another contract."""
    v1 = _extract(registry, echo, "invoice@1")
    v2 = _extract(registry, echo, "invoice@2")
    assert v1.artifact_id != v2.artifact_id
    assert v1.provenance.document_id == v2.provenance.document_id, "the parse is shared"


def test_extracting_one_major_does_not_disturb_the_other(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    before = _extract(registry, echo, "invoice@1").artifact_id
    _extract(registry, echo, "invoice@2")
    assert _extract(registry, echo, "invoice@1").artifact_id == before


# -- resolution failures -----------------------------------------------------


def test_a_bare_name_is_refused_from_the_library_core(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """SC-006, FR-014 -- there is no `latest` here.

    An edge may offer that convenience, but it resolves to a concrete version
    before extracting and records what it resolved to. A request whose meaning
    changes when the registry changes is not reproducible.
    """
    with pytest.raises(SchemaError, match="names no version"):
        _extract(registry, echo, "invoice")


@pytest.mark.parametrize("identity", ["invoice@latest", "invoice@newest", "invoice@v1", "invoice@"])
def test_no_spelling_of_latest_resolves(
    registry: SchemaRegistry, echo: EchoAdapter, identity: str
) -> None:
    with pytest.raises(SchemaError):
        _extract(registry, echo, identity)


def test_an_unregistered_version_names_the_ones_that_exist(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """EXT-12 -- no neighbouring version is substituted."""
    with pytest.raises(SchemaError) as caught:
        _extract(registry, echo, "invoice@3")
    assert caught.value.available == ("invoice@1", "invoice@2")
    assert "No neighbouring version is substituted" in str(caught.value)


def test_an_unregistered_name_names_every_identity(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    with pytest.raises(SchemaError) as caught:
        _extract(registry, echo, "purchase_order@1")
    assert caught.value.available == ("invoice@1", "invoice@2", "receipt@1")


def test_resolution_fails_before_anything_is_transmitted(registry: SchemaRegistry) -> None:
    """FR-041 -- a request that was always going to fail sends nothing."""

    class RecordingAdapter(EchoAdapter):
        calls = 0

        def complete(self, request, options):  # type: ignore[no-untyped-def]
            RecordingAdapter.calls += 1
            return super().complete(request, options)

    with pytest.raises(SchemaError):
        extract(
            make_document(DOCUMENT_TEXT),
            schema="invoice@3",
            registry=registry,
            adapter=RecordingAdapter.from_fixtures("tests/fixtures/echo"),
        )
    assert RecordingAdapter.calls == 0


# -- the version holds while the hash moves ----------------------------------


def test_a_description_edit_moves_the_hash_and_the_artifact_but_not_the_version(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """The whole point of ADR-0008's split, exercised end to end.

    Rewording a description changes what the model is told, so results change, so
    the extraction artifact must be invalidated. It does *not* change the contract
    a consumer depends on, so the version must not move.
    """
    original = _extract(registry, echo, "invoice@1")

    entry = registry.resolve("invoice@1")
    first, *rest = entry.schema.fields
    reworded = entry.schema.model_copy(
        update={"fields": (first.model_copy(update={"description": "reworded"}), *rest)}
    )

    edited_registry = SchemaRegistry()
    edited_registry.register(reworded, entry.prompt)
    after = extract(
        make_document(DOCUMENT_TEXT),
        schema="invoice@1",
        registry=edited_registry,
        adapter=echo,
    )

    assert after.provenance.schema_identity == "invoice@1", "the contract did not break"
    assert after.provenance.schema_hash != original.provenance.schema_hash
    assert after.artifact_id != original.artifact_id, "the extraction cache is invalidated"
    assert after.provenance.document_id == original.provenance.document_id, "the parse is reused"


def test_a_prompt_edit_moves_the_artifact_too(registry: SchemaRegistry, echo: EchoAdapter) -> None:
    """`prompt_hash` and `schema_hash` are distinct inputs; both are folded."""
    from docdoc.extraction.loader import PromptTemplate

    original = _extract(registry, echo, "invoice@1")
    entry = registry.resolve("invoice@1")

    edited_registry = SchemaRegistry()
    edited_registry.register(
        entry.schema, PromptTemplate(identity="invoice@1", text=entry.prompt.text + "\n\nAlso: x.")
    )
    after = extract(
        make_document(DOCUMENT_TEXT),
        schema="invoice@1",
        registry=edited_registry,
        adapter=echo,
    )
    assert after.provenance.schema_hash == original.provenance.schema_hash
    assert after.provenance.prompt_hash != original.provenance.prompt_hash
    assert after.artifact_id != original.artifact_id


def test_the_same_inputs_give_the_same_artifact_id(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """Without this, none of the invalidation assertions above mean anything."""
    first = _extract(registry, echo, "invoice@1")
    second = _extract(registry, echo, "invoice@1")
    assert first.artifact_id == second.artifact_id


def test_options_that_cannot_change_a_result_do_not_move_the_artifact(
    registry: SchemaRegistry, echo: EchoAdapter
) -> None:
    """The other half of the same guarantee (FR-027, EXT-22)."""
    base = ExtractionOptions()
    first = extract(
        make_document(DOCUMENT_TEXT),
        schema="invoice@1",
        registry=registry,
        adapter=echo,
        options=base,
    )
    second = extract(
        make_document(DOCUMENT_TEXT),
        schema="invoice@1",
        registry=registry,
        adapter=echo,
        options=base.model_copy(),
    )
    assert first.artifact_id == second.artifact_id
