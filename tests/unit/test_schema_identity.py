"""T016 — the two identities, and the one that surprises people (EXT-6…EXT-9).

``schema_version`` answers "did the consumer contract change?".
``schema_hash`` answers "did anything result-affecting change?".

They are different questions, which is the entire content of ADR-0008, and the
tests below are what stop the two collapsing back into one number.
"""

from __future__ import annotations

import pathlib

from docdoc.extraction import load_schema, schema_hash_for
from docdoc.extraction.schema import Schema

SCHEMAS = pathlib.Path("schemas")


def _invoice() -> Schema:
    return load_schema(SCHEMAS / "invoice@1.json")


def test_hash_is_content_addressed() -> None:
    assert schema_hash_for(_invoice()).startswith("sha256:")
    assert schema_hash_for(_invoice()) == schema_hash_for(_invoice())


def test_reordering_fields_does_not_move_the_hash() -> None:
    """EXT-7 -- formatting is not a change (ADR-0002's key sorting does the work)."""
    schema = _invoice()
    shuffled = schema.model_copy(update={"fields": tuple(reversed(schema.fields))})
    assert schema_hash_for(shuffled) == schema_hash_for(schema)


def test_editing_a_description_moves_the_hash() -> None:
    """A description steers the model, so it changes results, so it is hashed."""
    schema = _invoice()
    first, *rest = schema.fields
    edited = schema.model_copy(
        update={"fields": (first.model_copy(update={"description": "reworded"}), *rest)}
    )
    assert schema_hash_for(edited) != schema_hash_for(schema)


def test_editing_a_constraint_moves_the_hash_even_though_the_wire_never_sees_it() -> None:
    """research.md R3 -- the finding most likely to be reported as a bug.

    ``minimum`` is dropped from the request the provider sees, because its
    structured-output subset cannot express it. It is still hashed, because it
    changes what the result *means* to Milestone 5. So editing it invalidates the
    extraction cache while changing nothing the model reads.
    """
    schema = _invoice()
    total = next(f for f in schema.fields if f.name == "total")
    edited_total = total.model_copy(update={"constraints": {"minimum": 1}})
    edited = schema.model_copy(
        update={"fields": tuple(edited_total if f.name == "total" else f for f in schema.fields)}
    )
    assert schema_hash_for(edited) != schema_hash_for(schema)


def test_a_bare_version_bump_does_not_move_the_hash() -> None:
    """EXT-9 -- the subtle one, pinned deliberately.

    The hash excludes name and version. A pure ``@1`` -> ``@2`` bump with no
    other edit did not change the result, so the hash is right not to move. The
    two extractions still get different artifact ids, because the *identity* is
    folded into ``options_hash`` separately.
    """
    schema = _invoice()
    bumped = schema.model_copy(update={"version": 2})
    assert bumped.identity == "invoice@2"
    assert schema_hash_for(bumped) == schema_hash_for(schema)


def test_invoice_v2_differs_from_v1_because_it_added_a_field() -> None:
    v1 = load_schema(SCHEMAS / "invoice@1.json")
    v2 = load_schema(SCHEMAS / "invoice@2.json")
    assert schema_hash_for(v1) != schema_hash_for(v2)
