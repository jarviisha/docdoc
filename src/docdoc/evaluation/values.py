"""Typed values, and the one rule that keeps labels comparable to predictions.

A golden set is authored in JSON (FR-022), so every label value arrives as a JSON
scalar: a string, a number, or a boolean. A recorded prediction arrives the same
way, because ``ExtractionResult.values`` is typed ``dict[str, Any]`` and pydantic
cannot know what a JSON string was before it was serialized.

Both sides must land on the **same Python type**, or `comparators@1` fails every
comparison: its type-identity gate is deliberate (EVA-12a), so ``"1240.00"``
against ``Decimal("1240.00")`` is a mismatch and the report calls a perfect
extraction incorrect. That failure is silent, uniform, and looks exactly like a
model that cannot read decimals.

**So the rules here are `conform`'s rules, deliberately.** ``_coerce`` in
:mod:`docdoc.extraction.conform` decides what a declared ``FieldType`` can carry
when the model answers; this decides the same for a label and for a replayed
prediction. They are the same question asked at two moments, and the moment two
copies of it drift is the moment a dataset stops describing the pipeline it
scores. Reimplemented rather than imported for one reason: ``conform`` raises
``ExtractionError`` and reports through an extraction context, and an authoring
mistake in a dataset is not an extraction failure.

The refusal this powers is EVA-5b's fourth clause — *a label whose value the
declared ``FieldType`` cannot carry* — and it fires at load, like every other
authoring check, because a label that cannot be typed scores zero forever for a
reason nobody can see.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "SchemaFacts",
    "carry",
    "schema_facts",
    "strip_indices",
]


def strip_indices(field_path: str) -> str:
    """``line_items[2].amount`` -> ``line_items.amount``, the form a schema declares.

    A schema declares a repeating group's fields once; labels and predictions
    address each entry separately. Every lookup that crosses that boundary --
    the type of a label's value, the comparator that decided it, whether the path
    resolves at all -- goes through here, so the three of them cannot disagree
    about what "the same field" means.
    """
    out: list[str] = []
    depth = 0
    for char in field_path:
        if char == "[":
            depth += 1
        elif char == "]":
            depth -= 1
        elif depth == 0:
            out.append(char)
    return "".join(out)


#: Cardinalities that hold other fields rather than a value. A label addresses a
#: scalar; a key field must be one.
_CONTAINERS = ("group", "repeating_group")

#: The Python type each declared type lands on. Used only to recognise a value
#: that has already been coerced -- the parsing rules below are the authority on
#: what may *become* one of these.
_PYTHON_TYPES: dict[str, type] = {
    "string": str,
    "boolean": bool,
    "integer": int,
    "number": float,
    "decimal": Decimal,
    "date": date,
    "datetime": datetime,
}


def _already_typed(raw: Any, kind: str) -> bool:
    """Whether ``raw`` is exactly the type ``kind`` declares.

    ``type(...) is`` rather than ``isinstance``, for the reason `comparators@1`
    gives: ``bool`` subclasses ``int`` and ``datetime`` subclasses ``date``, so an
    isinstance check would wave a boolean through as an integer and a datetime
    through as a date -- and the label would then never match a correctly typed
    prediction.
    """
    expected = _PYTHON_TYPES.get(kind)
    return expected is not None and type(raw) is expected


def carry(raw: Any, field_type: str | None) -> Any:
    """Parse a JSON scalar into the Python type ``field_type`` declares.

    Returns ``raw`` unchanged when no type is declared -- a caller holding no
    schema registry still gets a dataset, it just gets no type checking with it.

    Raises:
        ValueError: the declared type cannot carry this value. The message names
            the type and what arrived, because "invalid label" sends the author
            back to guess which of the two is wrong.
    """
    if field_type is None or raw is None:
        return raw

    kind = str(field_type)
    if _already_typed(raw, kind):
        # Idempotent, and that is load-bearing rather than a convenience. The
        # loader coerces on the way in and `validate_golden_set` re-checks the
        # set it just built; a `carry` that only accepted the JSON form would
        # refuse its own output, so every dataset with a date in it would fail to
        # load. It also lets a golden set be constructed in memory -- by a test,
        # or by `promote()` -- and validated by the same rules as one read from
        # disk.
        return raw

    try:
        if kind == "string":
            if not isinstance(raw, str):
                raise TypeError(type(raw).__name__)
            return raw
        if kind == "boolean":
            # Checked before `integer`, and the order is not arbitrary: `bool`
            # subclasses `int`, so an integer check reached first would accept
            # `True` as an integer label and `comparators@1` would then never
            # match it against a real one.
            if not isinstance(raw, bool):
                raise TypeError(type(raw).__name__)
            return raw
        if kind == "integer":
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise TypeError(type(raw).__name__)
            return raw
        if kind == "number":
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise TypeError(type(raw).__name__)
            return float(raw)
        if kind == "decimal":
            # A decimal travels as a string, never as a JSON number, for the
            # reason Milestone 3 gives: the float conversion is lossy and an
            # invoice total must not inherit the loss.
            if not isinstance(raw, str):
                raise TypeError(type(raw).__name__)
            return Decimal(raw)
        if kind == "date":
            if not isinstance(raw, str):
                raise TypeError(type(raw).__name__)
            return date.fromisoformat(raw)
        if kind == "datetime":
            if not isinstance(raw, str):
                raise TypeError(type(raw).__name__)
            return datetime.fromisoformat(raw)
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise ValueError(f"a {kind} field cannot carry {raw!r}: {exc}") from exc

    # An unknown type name is not silently passed through. A typo in a schema
    # description would otherwise disable type checking for that field alone,
    # which is the least visible way for this to fail.
    raise ValueError(f"unknown field type {kind!r}")


class SchemaFacts:
    """What the loader needs to know about a schema, without importing one.

    Three maps, each keyed by schema identity:

    ``paths``
        every declared field path, indices stripped. An unresolvable label path
        is refused against this (EVA-5b).
    ``scalar_paths``
        the subset that are scalars. An entry key must name one (EVA-4a).
    ``field_types``
        path -> declared type name, which is what :func:`carry` reads.

    Built by :func:`schema_facts` from whatever the caller's registry describes,
    so this package never imports a schema registry and the FR-007 import
    contract stays green.
    """

    __slots__ = ("field_types", "paths", "scalar_paths")

    def __init__(
        self,
        *,
        paths: dict[str, frozenset[str]],
        scalar_paths: dict[str, frozenset[str]],
        field_types: dict[str, Mapping[str, str]],
    ) -> None:
        self.paths = paths
        self.scalar_paths = scalar_paths
        self.field_types = field_types

    def types_for(self, schema_identity: str) -> Mapping[str, str]:
        return self.field_types.get(schema_identity, {})


def schema_facts(descriptions: Iterable[Any]) -> SchemaFacts:
    """Fold ``SchemaRegistry.describe()`` output into the maps the loader reads.

    Takes any object carrying ``identity`` and ``fields``, where ``fields`` is
    the ``(path, type, cardinality, required, description)`` rows Milestone 3's
    ``describe()`` returns. Duck-typed on purpose: naming
    ``docdoc.extraction.registry.SchemaDescription`` here would put the schema
    loader -- and through it the adapter registry, and through that a provider
    SDK -- into this package's import graph, which is exactly the mistake the
    FR-007 contract fires on.
    """
    paths: dict[str, frozenset[str]] = {}
    scalar_paths: dict[str, frozenset[str]] = {}
    field_types: dict[str, Mapping[str, str]] = {}

    for description in descriptions:
        identity = description.identity
        declared: set[str] = set()
        scalars: set[str] = set()
        types: dict[str, str] = {}
        for path, type_name, cardinality, *_ in description.fields:
            declared.add(path)
            if cardinality not in _CONTAINERS:
                scalars.add(path)
                if type_name:
                    types[path] = type_name
        paths[identity] = frozenset(declared)
        scalar_paths[identity] = frozenset(scalars)
        field_types[identity] = types

    return SchemaFacts(paths=paths, scalar_paths=scalar_paths, field_types=field_types)
