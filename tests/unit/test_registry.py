"""T019 — the registry (EXT-10…EXT-13).

Concrete versions only, concurrent majors, no shadowing, and registration that is
all-or-nothing. The last one is the property that stops a failed load from leaving
a half-registered schema for someone else to trip over.
"""

from __future__ import annotations

import pathlib

import pytest

from docdoc.extraction import SchemaError, SchemaRegistry, default_registry, load_schema
from docdoc.extraction.loader import PromptTemplate

SCHEMAS = pathlib.Path("schemas")
BAD = pathlib.Path("tests/fixtures/schemas")


@pytest.fixture
def registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths(["schemas"])


def test_from_paths_loads_every_schema_with_its_prompt(registry: SchemaRegistry) -> None:
    assert registry.identities() == ("invoice@1", "invoice@2", "receipt@1")
    assert len(registry) == 3


def test_resolve_requires_a_concrete_version(registry: SchemaRegistry) -> None:
    """EXT-10 -- there is no `latest`, and there is no partial match either."""
    with pytest.raises(SchemaError, match="names no version") as caught:
        registry.resolve("invoice")
    assert caught.value.identity == "invoice"

    with pytest.raises(SchemaError, match="non-numeric version"):
        registry.resolve("invoice@latest")


def test_concurrent_majors_do_not_shadow_each_other(registry: SchemaRegistry) -> None:
    """EXT-11 -- @1 and @2 are two schemas, not one with a newer edition."""
    v1 = registry.resolve("invoice@1")
    v2 = registry.resolve("invoice@2")
    assert v1.identity == "invoice@1"
    assert v2.identity == "invoice@2"
    assert v1.schema_hash != v2.schema_hash
    assert v1.prompt.text != v2.prompt.text
    # Resolving one must not have disturbed the other.
    assert registry.resolve("invoice@1").schema_hash == v1.schema_hash


def test_unknown_version_names_the_ones_that_exist(registry: SchemaRegistry) -> None:
    """EXT-12 -- an error that says only "not found" sends the caller to read the registry."""
    with pytest.raises(SchemaError) as caught:
        registry.resolve("invoice@9")
    assert caught.value.available == ("invoice@1", "invoice@2")
    assert "invoice@1, invoice@2" in str(caught.value)
    assert "No neighbouring version is substituted" in str(caught.value)


def test_unknown_name_names_every_registered_identity(registry: SchemaRegistry) -> None:
    with pytest.raises(SchemaError) as caught:
        registry.resolve("purchase_order@1")
    assert caught.value.available == ("invoice@1", "invoice@2", "receipt@1")


def test_registering_the_same_identity_twice_is_refused(registry: SchemaRegistry) -> None:
    schema = load_schema(SCHEMAS / "invoice@1.json")
    with pytest.raises(SchemaError, match="already registered"):
        registry.register(schema, PromptTemplate(identity="invoice@1", text="x"))


def test_a_prompt_keyed_to_another_identity_is_refused() -> None:
    """A prompt attached to the wrong schema is a silent quality failure."""
    registry = SchemaRegistry()
    schema = load_schema(SCHEMAS / "invoice@1.json")
    with pytest.raises(SchemaError, match="keyed to"):
        registry.register(schema, PromptTemplate(identity="receipt@1", text="x"))


def test_a_failed_load_leaves_the_registry_untouched() -> None:
    """EXT-13 -- all-or-nothing, so there is no half-registered schema.

    ``tests/fixtures/schemas`` holds one loadable schema (``no_prompt@1``) with no
    prompt beside it, plus several that fail structurally. Loading the directory
    must fail *and* leave nothing behind.
    """
    registry = SchemaRegistry()
    before = registry.identities()
    with pytest.raises(SchemaError):
        SchemaRegistry.from_paths([BAD])
    assert registry.identities() == before == ()


def test_describe_reads_the_schema_without_a_model_call(registry: SchemaRegistry) -> None:
    """FR-018 -- inspectable before anything is extracted."""
    described = registry.describe("invoice@1")
    assert described.identity == "invoice@1"
    assert described.schema_hash == registry.resolve("invoice@1").schema_hash
    paths = described.field_names
    assert "total" in paths
    assert "supplier.legal_name" in paths, "a group's children are described too"
    assert "line_items.amount" in paths
    rows = {path: row for path, *row in described.fields}
    assert rows["total"][0] == "decimal"
    assert rows["line_items"][1] == "repeating_group"
    assert rows["total"][3], "the description is what tells a reader what to look for"


def test_describe_refuses_an_unknown_identity(registry: SchemaRegistry) -> None:
    with pytest.raises(SchemaError):
        registry.describe("invoice@9")


def test_contains_and_len_reflect_registration(registry: SchemaRegistry) -> None:
    assert "invoice@1" in registry
    assert "invoice@9" not in registry


def test_a_missing_directory_is_an_error_not_an_empty_registry() -> None:
    """Silently returning nothing would look like "no schemas are configured"."""
    with pytest.raises(SchemaError, match="not a directory"):
        SchemaRegistry.from_paths(["schemas/does-not-exist"])


def test_default_registry_with_no_paths_is_empty_not_bundled() -> None:
    """A schema is a deployment's data, so docdoc ships none of its own."""
    assert default_registry().identities() == ()
    assert default_registry(["schemas"]).identities() == (
        "invoice@1",
        "invoice@2",
        "receipt@1",
    )
