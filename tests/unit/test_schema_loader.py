"""T013 — loading schemas, and every way a schema is refused (EXT-1…EXT-5, FR-050).

Every defect is caught at *load* time, not at first use. A schema that fails
somewhere far from the file that caused it is a schema nobody fixes.
"""

from __future__ import annotations

import pathlib

import pytest

from docdoc.extraction import SchemaError, load_prompt, load_schema, prompt_path_for
from docdoc.extraction.schema import Cardinality, FieldType

SCHEMAS = pathlib.Path("schemas")
BAD = pathlib.Path("tests/fixtures/schemas")


def test_invoice_loads_with_every_cardinality() -> None:
    schema = load_schema(SCHEMAS / "invoice@1.json")
    assert schema.identity == "invoice@1"
    by_name = {f.name: f for f in schema.fields}
    assert by_name["total"].type is FieldType.DECIMAL
    assert by_name["supplier"].cardinality is Cardinality.GROUP
    assert by_name["line_items"].cardinality is Cardinality.REPEATING_GROUP
    assert "total" in schema.field_paths()
    assert "line_items.amount" in schema.field_paths()


def test_every_registered_schema_has_a_prompt() -> None:
    for path in sorted(SCHEMAS.glob("*.json")):
        schema = load_schema(path)
        prompt = load_prompt(schema, prompt_path_for(path))
        assert prompt.identity == schema.identity
        assert prompt.text.strip()


@pytest.mark.parametrize(
    ("fixture", "expected"),
    [
        ("malformed.json", "not valid JSON"),
        ("unknown_type.json", "type"),
        ("duplicate_field.json", "duplicate field name"),
        ("over_nested.json", "repetition is bounded to one level"),
        ("unknown_constraint.json", "unrecognised constraint key"),
        ("Uppercase@1.json", "Case is significant"),
    ],
)
def test_defects_are_refused_at_load_time(fixture: str, expected: str) -> None:
    """EXT-1…EXT-5 -- and the message names the defect, not just 'invalid'."""
    with pytest.raises(SchemaError) as caught:
        load_schema(BAD / fixture)
    assert expected in str(caught.value)
    assert caught.value.path is not None, "a load failure must name the file"


def test_repetition_bound_names_the_offending_path() -> None:
    """EXT-3 -- 'somewhere in this schema' is not an actionable message."""
    with pytest.raises(SchemaError) as caught:
        load_schema(BAD / "over_nested.json")
    assert "line_items.taxes" in str(caught.value)


def test_missing_prompt_is_refused_at_registration_not_at_first_use() -> None:
    """A schema that reaches a model with no instructions fails silently."""
    path = BAD / "no_prompt@1.json"
    schema = load_schema(path)  # the schema itself is fine
    with pytest.raises(SchemaError, match="has no prompt"):
        load_prompt(schema, prompt_path_for(path))
