"""The registry knows each entry's source, and console rows merge over files."""

from __future__ import annotations

import pytest

from docdoc.extraction import SchemaRegistry
from docdoc.extraction.loader import PromptTemplate, parse_schema, prompt_from_text
from docdoc.extraction.schema import Schema


def schema(name: str, version: int) -> Schema:
    return parse_schema(
        {
            "name": name,
            "version": version,
            "fields": [{"name": "total", "type": "decimal", "description": "The total."}],
        }
    )


def prompt(identity: str) -> PromptTemplate:
    name, version = identity.split("@")
    return prompt_from_text(schema(name, int(version)), "Extract the total.")


@pytest.fixture
def file_registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths(["schemas"])


def test_console_entry_is_added_and_marked(file_registry: SchemaRegistry) -> None:
    merged = file_registry.with_console([(schema("invoice", 3), prompt("invoice@3"))])
    assert merged.resolve("invoice@3").source == "console"
    assert "invoice@3" not in file_registry  # original untouched


def test_console_entry_shadows_a_file_and_is_reported(file_registry: SchemaRegistry) -> None:
    merged = file_registry.with_console([(schema("invoice", 2), prompt("invoice@2"))])
    assert merged.resolve("invoice@2").source == "console"
    assert merged.shadowed == ("invoice@2",)
    assert merged.resolve("receipt@1").source == "file"
    assert file_registry.resolve("invoice@2").source == "file"
    assert file_registry.shadowed == ()


def test_entries_are_sorted_by_identity(file_registry: SchemaRegistry) -> None:
    merged = file_registry.with_console([(schema("aaa", 1), prompt("aaa@1"))])
    assert [e.identity for e in merged.entries()] == sorted(merged.identities())
