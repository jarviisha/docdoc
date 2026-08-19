"""Schemas exercising every recognised constraint key, and the pairings FR-025 refuses.

These live under `tests/` rather than in `schemas/` on purpose. Adding a file to
the shipped registry would move the committed snapshot in
`tests/fixtures/snapshots/schema_hashes.json`, and SC-019 is the requirement that
this milestone does not move it.
"""

from __future__ import annotations

from typing import Any

from docdoc.extraction.schema import Cardinality, FieldSpec, FieldType, Schema

__all__ = [
    "EVERY_CONSTRAINT_KEY",
    "INCOMPATIBLE_PAIRINGS",
    "constraint_schema",
    "invoice_schema",
]


def constraint_schema(
    constraints: dict[str, Any],
    *,
    field_type: FieldType = FieldType.STRING,
    name: str = "probe",
) -> Schema:
    """A one-field schema carrying one set of constraints. The unit of the constraint tests."""
    return Schema(
        name="probe_schema",
        version=1,
        fields=(FieldSpec(name=name, type=field_type, constraints=constraints),),
    )


#: One well-typed declaration per recognised key, so a test can iterate the set
#: rather than list it again. SC-005 fails the build if a key ever appears in
#: `CONSTRAINT_KEYS` without an entry here and an enforcement path behind it.
EVERY_CONSTRAINT_KEY: dict[str, tuple[FieldType, dict[str, Any]]] = {
    "enum": (FieldType.STRING, {"enum": ["EUR", "USD", "VND"]}),
    "const": (FieldType.STRING, {"const": "INVOICE"}),
    "pattern": (FieldType.STRING, {"pattern": r"INV-\d{4}-\d{3}"}),
    "minimum": (FieldType.DECIMAL, {"minimum": 0}),
    "maximum": (FieldType.DECIMAL, {"maximum": 1000000}),
    "multiple_of": (FieldType.DECIMAL, {"multiple_of": "0.01"}),
    "min_length": (FieldType.STRING, {"min_length": 3}),
    "max_length": (FieldType.STRING, {"max_length": 64}),
}

#: Declarations that must be refused when the schema loads (FR-025).
#:
#: Each is a constraint that *could not be enforced* against the declared type.
#: Accepting one would produce a check that silently never runs, which is the
#: same defect as an unenforced constraint wearing the costume of a check.
INCOMPATIBLE_PAIRINGS: tuple[tuple[str, FieldType, dict[str, Any]], ...] = (
    ("a numeric bound on a boolean", FieldType.BOOLEAN, {"minimum": 0}),
    ("a numeric bound on a string", FieldType.STRING, {"maximum": 10}),
    ("a length bound on a number", FieldType.NUMBER, {"max_length": 5}),
    ("a length bound on a date", FieldType.DATE, {"min_length": 1}),
    ("a pattern on an integer", FieldType.INTEGER, {"pattern": r"\d+"}),
    ("a multiple on a date", FieldType.DATE, {"multiple_of": 1}),
    ("a multiple on a string", FieldType.STRING, {"multiple_of": 2}),
)


#: Declarations whose *value* cannot be evaluated (FR-019, SC-005).
#:
#: Distinct from `INCOMPATIBLE_PAIRINGS` above, which is about the key against
#: the field's type. These are well-matched keys carrying nonsense — and until a
#: convergence pass went looking, most of them did not raise: the evaluator could
#: not read the declaration, so the comparison could not be made, so the check
#: reported **passed** for every value. A constraint that always passes is a rule
#: that lies, and this list is what stops one being written.
MALFORMED_DECLARATIONS: tuple[tuple[str, FieldType, dict[str, Any]], ...] = (
    ("an unparseable numeric bound", FieldType.DECIMAL, {"minimum": "not-a-number"}),
    ("a null bound", FieldType.DECIMAL, {"maximum": None}),
    ("a boolean bound", FieldType.DECIMAL, {"minimum": True}),
    ("an unparseable multiple", FieldType.DECIMAL, {"multiple_of": "abc"}),
    ("a multiple of zero", FieldType.DECIMAL, {"multiple_of": 0}),
    ("an unparseable date bound", FieldType.DATE, {"minimum": "not-a-date"}),
    ("a date bound that is not a string", FieldType.DATE, {"maximum": 2026}),
    ("a length bound as a string", FieldType.STRING, {"max_length": "abc"}),
    ("a fractional length bound", FieldType.STRING, {"min_length": 3.7}),
    ("a negative length bound", FieldType.STRING, {"min_length": -1}),
    ("an enum as a bare string", FieldType.STRING, {"enum": "EUR"}),
    ("an empty enum", FieldType.STRING, {"enum": []}),
    ("a const as a list", FieldType.STRING, {"const": ["EUR"]}),
    ("a null const", FieldType.STRING, {"const": None}),
    ("a pattern that is not text", FieldType.STRING, {"pattern": 42}),
)


def invoice_schema(*, rules: tuple[Any, ...] = ()) -> Schema:
    """A realistic invoice: scalars, a group, a repeating group, and every kind of constraint.

    Shaped so that one document can exercise requiredness, each constraint, the
    grounding policy, and every rule kind — which is what makes the integration
    scenarios a single readable story rather than eight unrelated ones.
    """
    return Schema(
        name="probe_invoice",
        version=1,
        rules=rules,
        fields=(
            FieldSpec(
                name="number",
                type=FieldType.STRING,
                required=True,
                constraints={"pattern": r"INV-\d{4}-\d{3}"},
            ),
            FieldSpec(
                name="currency",
                type=FieldType.STRING,
                required=True,
                constraints={"enum": ["EUR", "USD", "VND"]},
            ),
            FieldSpec(name="issue_date", type=FieldType.DATE, required=True),
            FieldSpec(name="due_date", type=FieldType.DATE),
            FieldSpec(name="total", type=FieldType.DECIMAL, required=True),
            FieldSpec(name="notes", type=FieldType.STRING, constraints={"max_length": 40}),
            FieldSpec(
                name="supplier",
                cardinality=Cardinality.GROUP,
                fields=(
                    FieldSpec(name="name", type=FieldType.STRING, required=True),
                    FieldSpec(name="tax_id", type=FieldType.STRING),
                ),
            ),
            FieldSpec(
                name="line_items",
                cardinality=Cardinality.REPEATING_GROUP,
                fields=(
                    FieldSpec(name="description", type=FieldType.STRING, required=True),
                    FieldSpec(
                        name="quantity",
                        type=FieldType.INTEGER,
                        constraints={"minimum": 0},
                    ),
                    FieldSpec(name="unit_price", type=FieldType.DECIMAL),
                    FieldSpec(name="amount", type=FieldType.DECIMAL, required=True),
                ),
            ),
        ),
    )
