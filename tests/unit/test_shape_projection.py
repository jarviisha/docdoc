"""T021 — the wire projection (EXT-14, research.md R3).

The projection is where the two artifacts derived from a schema diverge, and the
divergence belongs to the provider rather than to us: its structured-output subset
cannot express numeric bounds, string-length bounds, or recursion.

The property Milestone 5 depends on is that what gets dropped from the wire is
still in the ``Schema`` and still inside ``schema_hash``. If a future change
"tidies up" by dropping unenforceable constraints at load time instead, extraction
keeps working and Milestone 5 silently loses its input -- which is why that is
asserted here rather than assumed.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from docdoc.extraction import PROJECTION_ID, load_schema, response_shape_for, schema_hash_for
from docdoc.extraction.shape import CLAIMED_TEXT_KEY, CONFIDENCE_KEY, VALUE_KEY

SCHEMAS = pathlib.Path("schemas")


@pytest.fixture
def invoice_shape() -> dict:
    return response_shape_for(load_schema(SCHEMAS / "invoice@1.json"))


def test_the_projection_is_versioned() -> None:
    """R7 -- it is code that changes what the model is asked for."""
    assert PROJECTION_ID == "response-shape@1"


def test_every_object_forbids_undeclared_properties(invoice_shape: dict) -> None:
    """The wire-level half of FR-008."""

    def walk(node: object) -> None:
        if isinstance(node, dict):
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(invoice_shape)


def test_each_field_is_asked_for_as_a_triple(invoice_shape: dict) -> None:
    """FR-003 is part of the enforced shape, not a hope about prompt wording."""
    total = invoice_shape["properties"]["total"]
    assert set(total["properties"]) == {VALUE_KEY, CLAIMED_TEXT_KEY, CONFIDENCE_KEY}
    assert set(total["required"]) == {VALUE_KEY, CLAIMED_TEXT_KEY, CONFIDENCE_KEY}


def test_absence_is_expressible(invoice_shape: dict) -> None:
    """FR-005 -- without a nullable value, a model with nothing to report must invent one."""
    for name in ("total", "due_date", "invoice_number"):
        types = invoice_shape["properties"][name]["properties"][VALUE_KEY]["type"]
        assert "null" in types, f"{name} must permit null"


def test_claimed_text_is_always_nullable_and_a_string(invoice_shape: dict) -> None:
    claimed = invoice_shape["properties"]["total"]["properties"][CLAIMED_TEXT_KEY]
    assert claimed["type"] == ["string", "null"]


def test_decimal_travels_as_a_string(invoice_shape: dict) -> None:
    """A total that has been through a JSON float is not the total that was printed."""
    assert invoice_shape["properties"]["total"]["properties"][VALUE_KEY]["type"] == [
        "string",
        "null",
    ]


def test_dates_carry_a_format(invoice_shape: dict) -> None:
    issue = invoice_shape["properties"]["issue_date"]["properties"][VALUE_KEY]
    assert issue["format"] == "date"


def test_enum_is_carried_because_the_provider_can_enforce_it(invoice_shape: dict) -> None:
    currency = invoice_shape["properties"]["currency"]["properties"][VALUE_KEY]
    assert currency["enum"] == ["USD", "EUR", "GBP", "VND", "JPY"]


@pytest.mark.parametrize("dropped", ["minimum", "maximum", "multiple_of", "max_length", "pattern"])
def test_unenforceable_constraints_never_reach_the_wire(invoice_shape: dict, dropped: str) -> None:
    """R3 -- the provider could not enforce these if asked."""
    assert dropped not in json.dumps(invoice_shape)


def test_what_the_wire_drops_is_still_hashed() -> None:
    """The property Milestone 5 depends on.

    ``invoice@1`` declares ``minimum`` on ``total`` and ``max_length`` on
    ``invoice_number``. Neither goes on the wire. Both must still be in the
    schema, and both must still move ``schema_hash`` -- otherwise "declared here,
    enforced there" is a story rather than a mechanism.
    """
    schema = load_schema(SCHEMAS / "invoice@1.json")
    by_name = {f.name: f for f in schema.fields}
    assert by_name["total"].constraints == {"minimum": 0}
    assert by_name["invoice_number"].constraints == {"max_length": 64}

    stripped_total = by_name["total"].model_copy(update={"constraints": {}})
    stripped = schema.model_copy(
        update={"fields": tuple(stripped_total if f.name == "total" else f for f in schema.fields)}
    )
    assert schema_hash_for(stripped) != schema_hash_for(schema)
    assert response_shape_for(stripped) == response_shape_for(schema), (
        "dropping an unenforceable constraint changes the hash but not the wire -- "
        "which is exactly the 'spurious cache miss' research.md R3 warns about"
    )


def test_a_group_is_an_object_and_a_repeating_group_is_an_array(invoice_shape: dict) -> None:
    assert invoice_shape["properties"]["supplier"]["type"] == "object"
    line_items = invoice_shape["properties"]["line_items"]
    assert line_items["type"] == "array"
    assert line_items["items"]["type"] == "object"
    assert set(line_items["items"]["properties"]) == {
        "description",
        "quantity",
        "unit_price",
        "amount",
    }


def test_descriptions_reach_the_model() -> None:
    """The description is the field's instruction; dropping it would be silent."""
    shape = response_shape_for(load_schema(SCHEMAS / "invoice@1.json"))
    assert "purchase-order" in shape["properties"]["invoice_number"]["description"]


def test_an_empty_schema_projects_to_an_empty_object() -> None:
    """A zero-field schema is legal and extracts nothing -- a boring answer, not an error."""
    schema = load_schema(SCHEMAS / "invoice@1.json").model_copy(update={"fields": ()})
    shape = response_shape_for(schema)
    assert shape == {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
    }
