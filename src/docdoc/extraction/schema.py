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

Milestone 5 extends that fourth invariant rather than inventing a second
convention: **cross-field rules are declared here as data and evaluated one
layer up** (`VAL-10`). This layer knows a rule's shape -- its kind, its operand
paths, their types and scoping -- and refuses a schema whose rule could not
work. It does not know what any rule *means*, and nothing here evaluates one.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from docdoc.extraction.errors import SchemaError

__all__ = [
    "CONSTRAINT_KEYS",
    "CONSTRAINT_TYPE_DOMAINS",
    "NAME_PATTERN",
    "RULE_ID_PATTERN",
    "SEVERITY_NAMES",
    "WIRE_ENFORCEABLE_CONSTRAINTS",
    "Cardinality",
    "FieldSpec",
    "FieldType",
    "Operator",
    "RuleKind",
    "RuleSpec",
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

#: Numeric types, in the sense of "can carry a bound or a multiple".
NUMERIC_TYPES = frozenset({FieldType.INTEGER, FieldType.NUMBER, FieldType.DECIMAL})

#: Temporal types, which are ordered and therefore boundable, but not divisible.
TEMPORAL_TYPES = frozenset({FieldType.DATE, FieldType.DATETIME})

#: Which declared types each constraint key can be enforced against (FR-025).
#:
#: ``None`` means "any scalar type" -- ``enum`` and ``const`` compare values, and
#: every declared type has equality. Everything else has a domain, and a
#: constraint outside its domain is an **authoring error rejected at load**:
#: a numeric bound on a boolean cannot be enforced, and silently ignoring it
#: would be a declared rule that lies, which is the defect Milestone 5 exists to
#: end. ``min_length`` and ``max_length`` carry two domains because they mean two
#: things -- character length for a string, entry count for a repeating group --
#: and the second is a *cardinality*, checked separately below.
CONSTRAINT_TYPE_DOMAINS: dict[str, frozenset[FieldType] | None] = {
    "enum": None,
    "const": None,
    "pattern": frozenset({FieldType.STRING}),
    "minimum": NUMERIC_TYPES | TEMPORAL_TYPES,
    "maximum": NUMERIC_TYPES | TEMPORAL_TYPES,
    "multiple_of": NUMERIC_TYPES,
    "min_length": frozenset({FieldType.STRING}),
    "max_length": frozenset({FieldType.STRING}),
}

#: A rule id is snake case like every other name here, so a finding's rule id
#: reads like the field paths beside it.
RULE_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")

#: The severities a schema author may assign to a rule they declare.
#:
#: Recognised here, applied one layer up -- the same split as ``CONSTRAINT_KEYS``.
#: The validation layer owns what a severity *does*; this layer owns only that
#: ``"errr"`` is not one of them, which is a typo worth catching at load rather
#: than at verdict time.
SEVERITY_NAMES = frozenset({"error", "warning", "info"})


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


class Operator(StrEnum):
    """The comparisons a ``comparison`` rule may make."""

    EQ = "=="
    NE = "!="
    LT = "<"
    LE = "<="
    GT = ">"
    GE = ">="


class RuleKind(StrEnum):
    """The closed cross-field vocabulary (VAL-1).

    Four kinds, and the closure is the point. An expression language would let a
    schema author write computation this layer cannot check and the validation
    layer cannot version; four named shapes can be checked here, evaluated there,
    and pinned under one ``RULE_VOCABULARY_VERSION``. Widening the vocabulary is
    a deliberate, versioned act rather than a schema author's improvisation
    (FR-027, FR-033).
    """

    #: ``sum(group[].member) == total``, within a declared tolerance.
    SUM_EQUALS = "sum_equals"

    #: ``a * b == c``, all three inside one repeating-group entry.
    PRODUCT_EQUALS = "product_equals"

    #: ``a <operator> b``, both of the same declared type.
    COMPARISON = "comparison"

    #: If ``a`` is present, ``b`` must be present.
    CONDITIONAL_PRESENCE = "conditional_presence"


#: How many operand paths each kind takes.
_RULE_ARITY: dict[RuleKind, int] = {
    RuleKind.SUM_EQUALS: 2,
    RuleKind.PRODUCT_EQUALS: 3,
    RuleKind.COMPARISON: 2,
    RuleKind.CONDITIONAL_PRESENCE: 2,
}

#: Which kinds compare numbers, and therefore may declare a tolerance.
_NUMERIC_KINDS = frozenset({RuleKind.SUM_EQUALS, RuleKind.PRODUCT_EQUALS})


class RuleSpec(BaseModel):
    """One declared cross-field obligation. Data, never code (VAL-7).

    Shape is checked at construction; meaning is applied by the validation layer.
    The operand paths are resolved against the schema in ``Schema``'s validator
    rather than here, because a rule alone does not know the fields it names.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str
    kind: RuleKind
    operands: tuple[str, ...]

    #: Required for ``comparison``, forbidden for every other kind.
    operator: Operator | None = None

    #: Numeric kinds only. **Declared, never inferred** (VAL-6): a default
    #: allowance would be an invisible loosening of every author's rule, and the
    #: values reaching validation are exact decimals, so exact equality is a
    #: meaningful default rather than a pedantic one (FR-030).
    tolerance: Decimal = Decimal(0)

    #: ``None`` means the documented default for this check kind (FR-040).
    severity: str | None = None

    @field_validator("id")
    @classmethod
    def _id_is_snake_case(cls, value: str) -> str:
        if not RULE_ID_PATTERN.match(value):
            raise ValueError(
                f"rule id {value!r} must match {RULE_ID_PATTERN.pattern} (lower snake case)"
            )
        return value

    @field_validator("severity")
    @classmethod
    def _severity_is_recognised(cls, value: str | None) -> str | None:
        if value is not None and value not in SEVERITY_NAMES:
            raise ValueError(
                f"unrecognised severity {value!r}; recognised severities are "
                f"{sorted(SEVERITY_NAMES)}"
            )
        return value

    @model_validator(mode="after")
    def _check_shape(self) -> RuleSpec:
        expected = _RULE_ARITY[self.kind]
        if len(self.operands) != expected:
            raise ValueError(
                f"rule {self.id!r} of kind {self.kind} takes {expected} operand path(s), "
                f"got {len(self.operands)}"
            )
        if self.kind is RuleKind.COMPARISON and self.operator is None:
            raise ValueError(f"rule {self.id!r} of kind {self.kind} must declare an operator")
        if self.kind is not RuleKind.COMPARISON and self.operator is not None:
            raise ValueError(
                f"rule {self.id!r} of kind {self.kind} must not declare an operator; "
                "only a comparison has one"
            )
        if self.tolerance < 0:
            raise ValueError(f"rule {self.id!r} declares a negative tolerance {self.tolerance}")
        if self.kind not in _NUMERIC_KINDS and self.tolerance != 0:
            raise ValueError(
                f"rule {self.id!r} of kind {self.kind} compares nothing numeric and "
                "must not declare a tolerance"
            )
        return self


class Schema(BaseModel):
    """A versioned declaration of the fields a document type carries.

    ``fields`` may be empty: a zero-field schema is legal and extracts nothing,
    which is a boring answer rather than an error.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    version: int = Field(ge=1)
    fields: tuple[FieldSpec, ...] = ()

    #: Declared here, applied by the validation layer (VAL-10). Default empty, and
    #: a schema that declares none hashes exactly as it did before Milestone 5
    #: (VAL-8, FR-053).
    rules: tuple[RuleSpec, ...] = ()

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

    @model_validator(mode="after")
    def _constraints_fit_their_fields(self) -> Schema:
        """FR-025 -- a constraint that cannot be enforced is rejected at load.

        The alternative is a constraint that is declared, hashed into schema
        identity, and silently skipped at verdict time. That is the same defect
        as an unenforced constraint, wearing the costume of a check.
        """
        for path in self.field_paths():
            field = self.field_at(path)
            assert field is not None
            for key in sorted(field.constraints):
                _check_constraint_domain(
                    field, key, field.constraints[key], path=path, identity=self.identity
                )
        return self

    @model_validator(mode="after")
    def _rules_resolve(self) -> Schema:
        """VAL-3, VAL-4, VAL-5 -- a rule that could not run is rejected at load."""
        seen: set[str] = set()
        for rule in self.rules:
            if rule.id in seen:
                raise SchemaError(
                    f"duplicate rule id {rule.id!r}; a finding names its rule, so two "
                    "rules sharing an id would produce findings nobody can attribute",
                    identity=self.identity,
                )
            seen.add(rule.id)
            self._check_rule_operands(rule)
        return self

    def _check_rule_operands(self, rule: RuleSpec) -> None:
        fields = []
        for operand in rule.operands:
            field = self.field_at(operand)
            if field is None:
                raise SchemaError(
                    f"rule {rule.id!r} names operand {operand!r}, which this schema "
                    "does not declare",
                    identity=self.identity,
                    field_path=operand,
                )
            if field.is_grouping:
                raise SchemaError(
                    f"rule {rule.id!r} names operand {operand!r}, which is a "
                    f"{field.cardinality} rather than a value. A rule compares values",
                    identity=self.identity,
                    field_path=operand,
                )
            fields.append(field)
        groups = [self.repeating_ancestor(operand) for operand in rule.operands]
        _check_rule_scoping(rule, groups, identity=self.identity)
        _check_rule_types(rule, fields, identity=self.identity)

    @property
    def identity(self) -> str:
        """``name@version`` -- the only form a request may name (EXT-5, FR-014)."""
        return f"{self.name}@{self.version}"

    def field_paths(self) -> tuple[str, ...]:
        """Every declared field, as dotted paths, in declaration order."""
        return tuple(_walk_paths(self.fields, prefix=""))

    def field_at(self, path: str) -> FieldSpec | None:
        """The field a dotted path names, or ``None``.

        Paths carry no entry index here: a rule names ``line_items.amount``, and
        *which* entry is a question about a result rather than about a schema.
        """
        fields = self.fields
        found: FieldSpec | None = None
        for part in path.split("."):
            found = next((child for child in fields if child.name == part), None)
            if found is None:
                return None
            fields = found.fields
        return found

    def repeating_ancestor(self, path: str) -> str | None:
        """The path of the repeating group containing ``path``, or ``None``.

        Answers the scoping question VAL-5 turns on: two operands are in the same
        entry iff they share this. ``None`` means the operand is a scalar of the
        document rather than of a line.
        """
        parts = path.split(".")
        for depth in range(len(parts) - 1, 0, -1):
            prefix = ".".join(parts[:depth])
            field = self.field_at(prefix)
            if field is not None and field.cardinality is Cardinality.REPEATING_GROUP:
                return prefix
        return None


def _check_constraint_domain(
    field: FieldSpec, key: str, declared: Any, *, path: str, identity: str
) -> None:
    """One constraint key against one field's declared type (FR-025), then its value."""
    is_length = key in ("min_length", "max_length")
    if field.cardinality is Cardinality.REPEATING_GROUP:
        # A length bound on a repeating group counts entries, which is the one
        # constraint a grouping field can carry.
        if is_length:
            return
        raise SchemaError(
            f"constraint {key!r} is declared on {path!r}, a repeating group. Only "
            "'min_length' and 'max_length' apply to one, and they count entries",
            identity=identity,
            field_path=path,
        )
    if field.cardinality is Cardinality.GROUP:
        raise SchemaError(
            f"constraint {key!r} is declared on {path!r}, a group. A group has no "
            "value to constrain; constrain its fields instead",
            identity=identity,
            field_path=path,
        )
    domain = CONSTRAINT_TYPE_DOMAINS[key]
    if domain is not None and field.type not in domain:
        raise SchemaError(
            f"constraint {key!r} is declared on {path!r}, whose declared type is "
            f"{field.type}. It applies to {sorted(str(item) for item in domain)}. A "
            "constraint that cannot be enforced must fail here rather than become a "
            "check that silently never runs",
            identity=identity,
            field_path=path,
        )
    _check_constraint_value(field, key, declared, path=path, identity=identity)


def _check_constraint_value(
    field: FieldSpec, key: str, declared: Any, *, path: str, identity: str
) -> None:
    """The declared *value* of a constraint, not only its key (FR-019, SC-005).

    The key and its type domain were already checked. What was not, until a
    convergence pass went looking, is whether the declared value can be evaluated
    at all -- and the failure mode was the worse one. ``{"minimum": "not a
    number"}`` did not crash: the evaluator could not parse the declaration, and
    a comparison it cannot make reported **passed**. A constraint that always
    passes is a rule that lies, which is the exact defect this milestone exists
    to end, one level in from the keys SC-005 protects.

    Unlike the pattern dialect, none of this needs the validation layer: whether
    a bound parses as a number, and whether an enum is a list, are questions about
    data. So they are answered here, beside the key and the domain.
    """
    if key == "enum":
        if not isinstance(declared, (list, tuple)) or not declared:
            raise SchemaError(
                f"constraint 'enum' on {path!r} must be a non-empty list, got "
                f"{type(declared).__name__}. A bare string is the usual slip and is "
                "the worst case: it would be read as a list of its characters, so the "
                "schema would reject the very value it was written to accept",
                identity=identity,
                field_path=path,
            )
        return
    if key == "const":
        if isinstance(declared, (list, tuple, dict, set)) or declared is None:
            raise SchemaError(
                f"constraint 'const' on {path!r} must be a single value, got "
                f"{type(declared).__name__}",
                identity=identity,
                field_path=path,
            )
        return
    if key == "pattern":
        # The *dialect* is the validation layer's to judge; that it is text at all
        # is this layer's.
        if not isinstance(declared, str):
            raise SchemaError(
                f"constraint 'pattern' on {path!r} must be a string, got {type(declared).__name__}",
                identity=identity,
                field_path=path,
            )
        return
    if key in ("min_length", "max_length"):
        _check_length_bound(declared, key, path=path, identity=identity)
        return
    _check_numeric_bound(field, declared, key, path=path, identity=identity)


def _check_length_bound(declared: Any, key: str, *, path: str, identity: str) -> None:
    """A length bound is a whole, non-negative count.

    A float is rejected rather than truncated: ``{"max_length": 3.7}`` silently
    became 3, so a value of four characters failed a bound its author never
    wrote. A numeric string is rejected for a blunter reason -- it used to reach
    ``int()`` mid-validation and escape as a bare ``ValueError``.
    """
    if isinstance(declared, bool) or not isinstance(declared, int):
        raise SchemaError(
            f"constraint {key!r} on {path!r} must be a whole number, got "
            f"{type(declared).__name__} ({declared!r}). A float would be truncated and a "
            "string would fail while a value was being checked, neither of which is what "
            "the author asked for",
            identity=identity,
            field_path=path,
        )
    if declared < 0:
        raise SchemaError(
            f"constraint {key!r} on {path!r} is negative ({declared}). No value has a "
            "length below zero, so the bound could never do anything",
            identity=identity,
            field_path=path,
        )


def _check_numeric_bound(
    field: FieldSpec, declared: Any, key: str, *, path: str, identity: str
) -> None:
    """``minimum``, ``maximum``, and ``multiple_of`` -- parseable in the field's own type."""
    if field.type in TEMPORAL_TYPES:
        if not isinstance(declared, str) or not _parses_as_temporal(declared, field.type):
            raise SchemaError(
                f"constraint {key!r} on {path!r} must be an ISO-8601 {field.type}, got "
                f"{declared!r}. A bound the comparison cannot read would be a check that "
                "never fails",
                identity=identity,
                field_path=path,
            )
        return
    parsed = _parses_as_decimal(declared)
    if parsed is None:
        raise SchemaError(
            f"constraint {key!r} on {path!r} must be a number, got "
            f"{type(declared).__name__} ({declared!r}). An unparseable bound is worse "
            "than a wrong one: the comparison cannot be made, so the check passes for "
            "every value",
            identity=identity,
            field_path=path,
        )
    if key == "multiple_of" and parsed == 0:
        raise SchemaError(
            f"constraint 'multiple_of' on {path!r} is zero. Every number is a multiple "
            "of zero only by convention, and no author means that",
            identity=identity,
            field_path=path,
        )


def _parses_as_decimal(declared: Any) -> Decimal | None:
    if isinstance(declared, bool) or declared is None:
        return None
    if isinstance(declared, Decimal):
        return declared
    if isinstance(declared, int):
        return Decimal(declared)
    if isinstance(declared, float):
        return Decimal(str(declared))
    if isinstance(declared, str):
        try:
            return Decimal(declared)
        except InvalidOperation:
            return None
    return None


def _parses_as_temporal(declared: str, kind: FieldType | None) -> bool:
    try:
        if kind is FieldType.DATETIME:
            datetime.fromisoformat(declared)
        else:
            date.fromisoformat(declared)
    except ValueError:
        return False
    return True


def _check_rule_scoping(rule: RuleSpec, groups: list[str | None], *, identity: str) -> None:
    """VAL-5 -- which operands may live inside which repeating group."""
    if rule.kind is RuleKind.SUM_EQUALS:
        member, total = groups
        if member is None:
            raise SchemaError(
                f"rule {rule.id!r} sums {rule.operands[0]!r}, which is not inside a "
                "repeating group. There is nothing to sum over",
                identity=identity,
                field_path=rule.operands[0],
            )
        if total is not None:
            raise SchemaError(
                f"rule {rule.id!r} compares a sum against {rule.operands[1]!r}, which is "
                f"inside repeating group {total!r}. The total of a group cannot be one of "
                "its own entries",
                identity=identity,
                field_path=rule.operands[1],
            )
        return
    if rule.kind is RuleKind.PRODUCT_EQUALS:
        if len({*groups}) != 1 or groups[0] is None:
            raise SchemaError(
                f"rule {rule.id!r} multiplies operands that are not all inside one "
                f"repeating group (found {groups!r}). A per-entry rule is evaluated once "
                "per entry, so its operands must all belong to that entry",
                identity=identity,
            )
        return
    # comparison and conditional_presence: both operands outside every repeating
    # group, or both inside the same one. Anything else has no single anchor.
    if len({*groups}) != 1:
        raise SchemaError(
            f"rule {rule.id!r} compares operands in different scopes (found {groups!r}). "
            "Both must be document-level, or both inside the same repeating group",
            identity=identity,
        )


def _check_rule_types(rule: RuleSpec, fields: list[FieldSpec], *, identity: str) -> None:
    """VAL-4 -- operands must be able to participate in the kind's arithmetic."""
    if rule.kind is RuleKind.CONDITIONAL_PRESENCE:
        # Presence is type-independent: this rule reads whether a value is there.
        return
    if rule.kind in _NUMERIC_KINDS:
        for operand, field in zip(rule.operands, fields, strict=True):
            if field.type not in NUMERIC_TYPES:
                raise SchemaError(
                    f"rule {rule.id!r} does arithmetic on {operand!r}, whose declared "
                    f"type is {field.type}. It applies to "
                    f"{sorted(str(item) for item in NUMERIC_TYPES)}",
                    identity=identity,
                    field_path=operand,
                )
        return
    left, right = fields
    if left.type is not right.type:
        raise SchemaError(
            f"rule {rule.id!r} compares {rule.operands[0]!r} ({left.type}) with "
            f"{rule.operands[1]!r} ({right.type}). A comparison across declared types "
            "would need a coercion rule, and coercing to make a comparison work is how "
            "a wrong answer looks right",
            identity=identity,
        )
    if rule.operator not in (Operator.EQ, Operator.NE) and left.type is FieldType.BOOLEAN:
        raise SchemaError(
            f"rule {rule.id!r} orders booleans, which have no order",
            identity=identity,
        )


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
