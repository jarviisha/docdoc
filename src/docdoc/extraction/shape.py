"""Projecting a schema onto what the provider can actually enforce.

This is the one place where the two artifacts derived from a schema diverge, and
the divergence is the provider's, not a design preference (research.md R3). Its
structured-output subset **cannot express** numeric bounds, string-length bounds,
complex array constraints, or recursive schemas. So:

- ``schema_hash`` covers the **whole** schema, constraints included. It answers
  what a result *means*.
- the response shape carries only the enforceable subset. It is what goes on the
  wire.

Editing a numeric bound therefore invalidates the extraction artifact while
changing nothing the model reads. That is correct under ADR-0008 and it looks
like a spurious cache miss, which is why it is documented rather than discovered.

Two spec decisions turn out to be forced rather than chosen, and it is worth
knowing which: field constraints are enforced by Milestone 5 because the provider
*could not* enforce them if asked (FR-006), and repetition is bounded because
recursion is not expressible at all (FR-048). Ours is merely stricter.

The projection is code that changes what the model is asked for, so it carries a
version -- ``response-shape@1`` -- exactly as the ingest layer's text-layer rule
does (research.md R7).
"""

from __future__ import annotations

from typing import Any

from docdoc.extraction.schema import (
    WIRE_ENFORCEABLE_CONSTRAINTS,
    Cardinality,
    FieldSpec,
    FieldType,
    Schema,
)

__all__ = ["CLAIMED_TEXT_KEY", "CONFIDENCE_KEY", "PROJECTION_ID", "VALUE_KEY", "response_shape_for"]

PROJECTION_ID = "response-shape@1"

VALUE_KEY = "value"
CLAIMED_TEXT_KEY = "claimed_text"
CONFIDENCE_KEY = "confidence"

#: How each declared type is asked for on the wire. ``decimal`` travels as a
#: string: asking for a JSON number would put an invoice total through a float,
#: and a total that has been through a float is not the total that was printed.
_JSON_TYPE: dict[FieldType, dict[str, Any]] = {
    FieldType.STRING: {"type": "string"},
    FieldType.INTEGER: {"type": "integer"},
    FieldType.NUMBER: {"type": "number"},
    FieldType.BOOLEAN: {"type": "boolean"},
    FieldType.DATE: {"type": "string", "format": "date"},
    FieldType.DATETIME: {"type": "string", "format": "date-time"},
    FieldType.DECIMAL: {"type": "string"},
}


def _nullable(shape: dict[str, Any]) -> dict[str, Any]:
    """Allow ``null`` alongside a type, so absence is expressible.

    Without this, a model with nothing to report has to invent a value. FR-005
    makes absence a first-class answer, and the wire shape has to permit it or
    the requirement is unreachable.
    """
    base = dict(shape)
    declared = base.pop("type")
    types = [declared, "null"] if isinstance(declared, str) else [*declared, "null"]
    return {"type": types, **base}


def _scalar_shape(field: FieldSpec) -> dict[str, Any]:
    assert field.type is not None  # guaranteed by FieldSpec's EXT-2 check
    value_shape = dict(_JSON_TYPE[field.type])

    for key in WIRE_ENFORCEABLE_CONSTRAINTS & set(field.constraints):
        value_shape[key] = field.constraints[key]

    return {
        "type": "object",
        "description": field.description,
        "properties": {
            VALUE_KEY: _nullable(value_shape),
            CLAIMED_TEXT_KEY: {
                "type": ["string", "null"],
                "description": (
                    "The text exactly as it appears in the document that this value was read "
                    "from, character for character, unmodified."
                ),
            },
            CONFIDENCE_KEY: {
                "type": ["number", "null"],
                "description": "Your own confidence in this value, if you have one.",
            },
        },
        "required": [VALUE_KEY, CLAIMED_TEXT_KEY, CONFIDENCE_KEY],
        "additionalProperties": False,
    }


def _object_shape(fields: tuple[FieldSpec, ...], *, description: str = "") -> dict[str, Any]:
    properties = {field.name: _field_shape(field) for field in fields}
    shape: dict[str, Any] = {
        "type": "object",
        "properties": properties,
        # Every declared field is required *on the wire* regardless of the
        # schema's own `required` flag, because absence is expressed as a null
        # value rather than an absent key. That keeps "the model did not answer"
        # (a shape failure) distinguishable from "the document does not contain
        # it" (a legitimate result) -- EXT-16.
        "required": list(properties),
        "additionalProperties": False,
    }
    if description:
        shape["description"] = description
    return shape


def _field_shape(field: FieldSpec) -> dict[str, Any]:
    if field.cardinality is Cardinality.SCALAR:
        return _scalar_shape(field)
    if field.cardinality is Cardinality.GROUP:
        return _object_shape(field.fields, description=field.description)
    return {
        "type": "array",
        "description": field.description,
        "items": _object_shape(field.fields),
    }


def response_shape_for(schema: Schema) -> dict[str, Any]:
    """The JSON Schema sent to the provider (EXT-14).

    Carries types, cardinality, ``enum``, ``const``, and string formats; sets
    ``additionalProperties: false`` at every object; asks for each field as a
    ``value``/``claimed_text``/``confidence`` triple. Drops numeric bounds,
    string-length bounds, and ``pattern`` -- all of which stay in the ``Schema``
    and stay inside ``schema_hash``, because that is the property Milestone 5
    depends on.
    """
    return _object_shape(schema.fields)
