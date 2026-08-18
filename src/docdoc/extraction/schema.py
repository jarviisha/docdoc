"""Schemas: the only place document-type knowledge lives (Principle VI).

A schema is authored as data (research.md R6) and validated structurally here.
Four invariants are enforced at construction, so an invalid schema cannot exist
as an object rather than merely failing later:

``EXT-1``
    Field names are unique among siblings.
``EXT-2``
    Children exist if and only if the cardinality is a group or a repeating
    group.
``EXT-3``
    **Repetition is bounded to one level.** A repeating group may contain
    scalars and nested groups, never another repeating group.
``EXT-4``
    Constraint keys are *recognised*, never *applied*. Whether a parseable value
    is acceptable is the validation stage's question (Principle VII), and the
    provider could not enforce most of these anyway (research.md R3).
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from docdoc.extraction.errors import SchemaError

__all__ = [
    "CONSTRAINT_KEYS",
    "NAME_PATTERN",
    "WIRE_ENFORCEABLE_CONSTRAINTS",
    "Cardinality",
    "FieldSpec",
    "FieldType",
    "Schema",
]

#: A schema name and a field name are both lower snake case. The pattern is what
#: makes EXT-5's case significance a *load* failure rather than two schemas whose
#: identities differ only in case.
NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


class FieldType(StrEnum):
    """The declared type of a scalar field.

    ``DECIMAL`` exists separately from ``NUMBER`` because an invoice total must
    not become a float. It travels as a string and parses to ``Decimal``.
    """

    STRING = "string"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    DECIMAL = "decimal"


class Cardinality(StrEnum):
    SCALAR = "scalar"
    GROUP = "group"
    REPEATING_GROUP = "repeating_group"


#: Constraints the provider's structured-output subset can enforce on the wire,
#: and which the projection therefore carries (research.md R3).
WIRE_ENFORCEABLE_CONSTRAINTS = frozenset({"enum", "const"})

#: Every constraint key a schema may declare. The ones outside
#: ``WIRE_ENFORCEABLE_CONSTRAINTS`` are carried in the schema, hashed into its
#: identity, and enforced by Milestone 5 -- the provider cannot express them.
CONSTRAINT_KEYS = WIRE_ENFORCEABLE_CONSTRAINTS | {
    "pattern",
    "minimum",
    "maximum",
    "multiple_of",
    "min_length",
    "max_length",
}


class FieldSpec(BaseModel):
    """One declared field."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    type: FieldType | None = None
    cardinality: Cardinality = Cardinality.SCALAR
    required: bool = False
    description: str = ""
    constraints: dict[str, Any] = Field(default_factory=dict)
    fields: tuple[FieldSpec, ...] = ()

    @field_validator("name")
    @classmethod
    def _name_is_snake_case(cls, value: str) -> str:
        if not NAME_PATTERN.match(value):
            raise ValueError(
                f"field name {value!r} must match {NAME_PATTERN.pattern} "
                "(lower snake case, starting with a letter)"
            )
        return value

    @field_validator("constraints")
    @classmethod
    def _constraints_are_recognised(cls, value: dict[str, Any]) -> dict[str, Any]:
        """EXT-4 -- recognised, never applied."""
        unknown = sorted(set(value) - CONSTRAINT_KEYS)
        if unknown:
            raise ValueError(
                f"unrecognised constraint key(s) {unknown}; "
                f"recognised keys are {sorted(CONSTRAINT_KEYS)}"
            )
        return value

    @model_validator(mode="after")
    def _check_structure(self) -> FieldSpec:
        self._check_children()
        self._check_sibling_names()
        self._check_repetition_bound()
        return self

    def _check_children(self) -> None:
        """EXT-2 -- children iff group or repeating group."""
        is_grouping = self.cardinality in (Cardinality.GROUP, Cardinality.REPEATING_GROUP)
        if is_grouping and not self.fields:
            raise ValueError(
                f"field {self.name!r} is a {self.cardinality} and must declare at least one child"
            )
        if not is_grouping and self.fields:
            raise ValueError(f"scalar field {self.name!r} must not declare children")
        if is_grouping and self.type is not None:
            raise ValueError(
                f"field {self.name!r} is a {self.cardinality} and must not declare a scalar type"
            )
        if not is_grouping and self.type is None:
            raise ValueError(f"scalar field {self.name!r} must declare a type")

    def _check_sibling_names(self) -> None:
        """EXT-1 -- unique among siblings."""
        seen: set[str] = set()
        for child in self.fields:
            if child.name in seen:
                raise ValueError(f"duplicate field name {child.name!r} under {self.name!r}")
            seen.add(child.name)

    def _check_repetition_bound(self) -> None:
        """EXT-3 -- one level of repetition, checked at every depth."""
        if self.cardinality is not Cardinality.REPEATING_GROUP:
            return
        nested = _find_repeating(self.fields, prefix=self.name)
        if nested is not None:
            raise ValueError(
                f"repetition is bounded to one level: repeating group {self.name!r} "
                f"contains another repeating group at {nested!r}. A repeating group may "
                "contain scalars and nested groups. Raising the bound later will not "
                "invalidate a schema that was already accepted"
            )

    @property
    def is_grouping(self) -> bool:
        return self.cardinality in (Cardinality.GROUP, Cardinality.REPEATING_GROUP)


def _find_repeating(fields: tuple[FieldSpec, ...], *, prefix: str) -> str | None:
    """The dotted path of the first repeating group at any depth, or ``None``."""
    for field in fields:
        path = f"{prefix}.{field.name}"
        if field.cardinality is Cardinality.REPEATING_GROUP:
            return path
        found = _find_repeating(field.fields, prefix=path)
        if found is not None:
            return found
    return None


class Schema(BaseModel):
    """A versioned declaration of the fields a document type carries.

    ``fields`` may be empty: a zero-field schema is legal and extracts nothing,
    which is a boring answer rather than an error.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: int = Field(ge=1)
    fields: tuple[FieldSpec, ...] = ()

    @field_validator("name")
    @classmethod
    def _name_is_snake_case(cls, value: str) -> str:
        if not NAME_PATTERN.match(value):
            raise ValueError(
                f"schema name {value!r} must match {NAME_PATTERN.pattern}. "
                "Case is significant, so an upper-case name is not a near-duplicate "
                "of a registered one -- it is unloadable (EXT-5)"
            )
        return value

    @model_validator(mode="after")
    def _names_unique(self) -> Schema:
        seen: set[str] = set()
        for field in self.fields:
            if field.name in seen:
                raise ValueError(f"duplicate field name {field.name!r} at the schema root")
            seen.add(field.name)
        return self

    @property
    def identity(self) -> str:
        """``name@version`` -- the only form a request may name (EXT-5, FR-014)."""
        return f"{self.name}@{self.version}"

    def field_paths(self) -> tuple[str, ...]:
        """Every declared field, as dotted paths, in declaration order."""
        return tuple(_walk_paths(self.fields, prefix=""))


def _walk_paths(fields: tuple[FieldSpec, ...], *, prefix: str) -> list[str]:
    paths: list[str] = []
    for field in fields:
        path = f"{prefix}{field.name}"
        paths.append(path)
        if field.is_grouping:
            paths.extend(_walk_paths(field.fields, prefix=f"{path}."))
    return paths


def parse_identity(identity: str) -> tuple[str, int]:
    """Split ``name@version``, refusing anything that is not a concrete identity.

    There is no ``latest`` and no partial match. A request whose meaning changes
    when the registry changes is not reproducible (ADR-0008, FR-014).
    """
    if "@" not in identity:
        raise SchemaError(
            f"schema identity {identity!r} names no version. "
            "Requests must name a concrete name@version; there is no 'latest'",
            identity=identity,
        )
    name, _, version_text = identity.partition("@")
    if not version_text.isdigit():
        raise SchemaError(
            f"schema identity {identity!r} has a non-numeric version. "
            "A version is a major integer, starting at 1",
            identity=identity,
        )
    return name, int(version_text)
