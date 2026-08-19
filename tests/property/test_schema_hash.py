"""T017 — `schema_hash` under Hypothesis (SC-005, EXT-7, EXT-8).

Two properties, and they pull in opposite directions, which is why both are here:

- **Insensitive to presentation.** Reordering fields, at any depth, never moves
  the hash. This one already failed once during implementation -- canonical JSON
  sorts object keys but preserves *list* order, and fields are a list -- so it is
  a regression test as much as a property.
- **Sensitive to meaning.** Any change to a field's name, type, cardinality,
  required flag, description, or constraints always moves it.

An implementation that satisfies only the first would hash every schema to the
same value; one that satisfies only the second is order-sensitive. The pair pins
the behaviour from both sides.
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from docdoc.extraction import schema_hash_for
from docdoc.extraction.schema import Cardinality, FieldSpec, FieldType, Schema

_NAMES = st.from_regex(r"[a-z][a-z0-9_]{0,11}", fullmatch=True)
_DESCRIPTIONS = st.text(min_size=0, max_size=24)

_ENUM = st.builds(lambda v: {"enum": v}, st.lists(st.text(max_size=4), min_size=1, max_size=3))
_BOUND = st.builds(lambda n: {"minimum": n}, st.integers(min_value=-5, max_value=5))
_LENGTH = st.builds(lambda n: {"max_length": n}, st.integers(min_value=1, max_value=64))

#: Type and constraints are drawn **together**, because Milestone 5's FR-025
#: makes an ill-matched pairing -- a numeric bound on a string, a length bound on
#: a boolean -- unconstructible rather than merely unenforceable. Drawing them
#: independently, as this strategy did while constraints were recognised and
#: never applied, now discards most of the search space at construction. Same
#: reasoning as the one-level repetition bound below: respect the invariant in
#: the strategy rather than filter for it.
_TYPED_SCALARS = st.one_of(
    st.tuples(st.just(FieldType.STRING), st.one_of(st.just({}), _LENGTH, _ENUM)),
    st.tuples(
        st.sampled_from([FieldType.INTEGER, FieldType.NUMBER, FieldType.DECIMAL]),
        st.one_of(st.just({}), _BOUND, _ENUM),
    ),
    st.tuples(
        st.sampled_from([FieldType.BOOLEAN, FieldType.DATE, FieldType.DATETIME]),
        st.one_of(st.just({}), _ENUM),
    ),
)


def _scalars(names: list[str]) -> st.SearchStrategy[tuple[FieldSpec, ...]]:
    """Scalar fields with guaranteed-unique sibling names (EXT-1)."""
    return st.tuples(
        *(
            st.builds(
                lambda typed, required, description, name=name: FieldSpec(
                    name=name,
                    type=typed[0],
                    cardinality=Cardinality.SCALAR,
                    required=required,
                    description=description,
                    constraints=typed[1],
                ),
                typed=_TYPED_SCALARS,
                required=st.booleans(),
                description=_DESCRIPTIONS,
            )
            for name in names
        )
    )


@st.composite
def _fields(draw: Any) -> tuple[FieldSpec, ...]:
    """A field list mixing scalars, one group, and one repeating group.

    Repetition stays at one level because EXT-3 refuses anything deeper at
    construction -- the strategy respects the bound rather than filtering on it,
    so Hypothesis never has to discard a draw.
    """
    names = draw(st.lists(_NAMES, min_size=1, max_size=4, unique=True))
    fields: list[FieldSpec] = list(draw(_scalars(names)))

    if draw(st.booleans()):
        inner = draw(st.lists(_NAMES, min_size=1, max_size=2, unique=True))
        group_name = draw(_NAMES.filter(lambda n: n not in names))
        fields.append(
            FieldSpec(
                name=group_name,
                cardinality=draw(st.sampled_from([Cardinality.GROUP, Cardinality.REPEATING_GROUP])),
                description=draw(_DESCRIPTIONS),
                fields=draw(_scalars(inner)),
            )
        )
    return tuple(fields)


_SCHEMAS = st.builds(
    Schema,
    name=st.just("probe"),
    version=st.integers(min_value=1, max_value=9),
    fields=_fields(),
)


@given(_SCHEMAS)
@settings(max_examples=200)
def test_the_hash_is_a_content_address(schema: Schema) -> None:
    digest = schema_hash_for(schema)
    assert digest.startswith("sha256:")
    assert schema_hash_for(schema) == digest


@given(_SCHEMAS)
@settings(max_examples=200)
def test_reordering_top_level_fields_never_moves_the_hash(schema: Schema) -> None:
    """EXT-7 -- declaration order is presentation, not meaning."""
    reversed_schema = schema.model_copy(update={"fields": tuple(reversed(schema.fields))})
    assert schema_hash_for(reversed_schema) == schema_hash_for(schema)


@given(_SCHEMAS)
@settings(max_examples=200)
def test_reordering_nested_fields_never_moves_the_hash(schema: Schema) -> None:
    """The depth the first implementation got wrong."""
    rebuilt = tuple(
        field.model_copy(update={"fields": tuple(reversed(field.fields))})
        if field.is_grouping
        else field
        for field in schema.fields
    )
    assert schema_hash_for(schema.model_copy(update={"fields": rebuilt})) == schema_hash_for(schema)


@given(_SCHEMAS)
@settings(max_examples=200)
def test_the_version_alone_never_moves_the_hash(schema: Schema) -> None:
    """EXT-9 -- a bare bump did not change the result, so the hash is right to hold."""
    bumped = schema.model_copy(update={"version": schema.version + 1})
    assert schema_hash_for(bumped) == schema_hash_for(schema)


def _replace_first(schema: Schema, **update: Any) -> Schema:
    first, *rest = schema.fields
    return schema.model_copy(update={"fields": (first.model_copy(update=update), *rest)})


@given(_SCHEMAS)
@settings(max_examples=200)
def test_editing_a_description_always_moves_the_hash(schema: Schema) -> None:
    """A description steers the model, so it changes results, so it is hashed."""
    original = schema.fields[0].description
    edited = _replace_first(schema, description=original + " (reworded)")
    assert schema_hash_for(edited) != schema_hash_for(schema)


@given(_SCHEMAS)
@settings(max_examples=200)
def test_flipping_required_always_moves_the_hash(schema: Schema) -> None:
    edited = _replace_first(schema, required=not schema.fields[0].required)
    assert schema_hash_for(edited) != schema_hash_for(schema)


@given(_SCHEMAS)
@settings(max_examples=200)
def test_renaming_a_field_always_moves_the_hash(schema: Schema) -> None:
    taken = {field.name for field in schema.fields}
    fresh = "z_renamed"
    while fresh in taken:
        fresh += "x"
    edited = _replace_first(schema, name=fresh)
    assert schema_hash_for(edited) != schema_hash_for(schema)


@given(_SCHEMAS)
@settings(max_examples=200)
def test_editing_a_constraint_always_moves_the_hash(schema: Schema) -> None:
    """EXT-8, and the case research.md R3 singles out.

    The constraint may never reach the wire. It still changes what the result
    *means* to Milestone 5, so it is hashed -- which is why editing one invalidates
    the extraction cache while changing nothing the model reads.
    """
    field = next((f for f in schema.fields if not f.is_grouping), None)
    if field is None:  # pragma: no cover - the strategy always yields one scalar
        return
    changed = dict(field.constraints)
    changed["minimum"] = changed.get("minimum", 0) + 1
    rebuilt = tuple(
        f.model_copy(update={"constraints": changed}) if f is field else f for f in schema.fields
    )
    edited = schema.model_copy(update={"fields": rebuilt})
    assert schema_hash_for(edited) != schema_hash_for(schema)


@given(_SCHEMAS)
@settings(max_examples=200)
def test_changing_a_scalar_type_always_moves_the_hash(schema: Schema) -> None:
    field = next((f for f in schema.fields if not f.is_grouping), None)
    if field is None:  # pragma: no cover
        return
    other = next(t for t in FieldType if t is not field.type)
    rebuilt = tuple(
        f.model_copy(update={"type": other}) if f is field else f for f in schema.fields
    )
    edited = schema.model_copy(update={"fields": rebuilt})
    assert schema_hash_for(edited) != schema_hash_for(schema)
