"""T010 — a schema whose rule or constraint cannot work is refused at load.

The point is not that bad input raises. It is *when*: at load, before any result
is validated. A rule or a constraint that reaches a validation run and cannot be
evaluated becomes a check that never ran, and a check that never ran is exactly
what this milestone exists to make impossible (FR-025, FR-029, FR-056, SC-014).
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from docdoc.extraction.errors import SchemaError
from docdoc.extraction.schema import CONSTRAINT_KEYS, RuleSpec, Schema
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation import schemas as schema_fixtures


def _invoice_with(rule_kwargs: dict) -> Schema:
    base = schema_fixtures.invoice_schema()
    return Schema(
        name=base.name,
        version=base.version,
        fields=base.fields,
        rules=(RuleSpec(**rule_kwargs),),
    )


class TestRules:
    def test_every_well_formed_kind_loads(self) -> None:
        schema = schema_fixtures.invoice_schema(rules=rule_fixtures.every_kind())
        assert len(schema.rules) == 4

    @pytest.mark.parametrize(
        ("reason", "kwargs"),
        rule_fixtures.INVALID_RULES,
        ids=[reason for reason, _ in rule_fixtures.INVALID_RULES],
    )
    def test_a_rule_that_cannot_work_is_refused(self, reason: str, kwargs: dict) -> None:
        with pytest.raises((SchemaError, ValidationError)):
            _invoice_with(kwargs)

    def test_a_duplicate_rule_id_is_refused(self) -> None:
        base = schema_fixtures.invoice_schema()
        with pytest.raises(SchemaError, match="duplicate rule id"):
            Schema(
                name=base.name,
                version=base.version,
                fields=base.fields,
                rules=(rule_fixtures.sum_rule(), rule_fixtures.sum_rule(tolerance="0.01")),
            )

    def test_the_error_names_the_rule_and_the_operand(self) -> None:
        with pytest.raises(SchemaError) as caught:
            _invoice_with(
                {
                    "id": "ghost",
                    "kind": rule_fixtures.RuleKind.COMPARISON,
                    "operands": ("due_date", "no_such_field"),
                    "operator": rule_fixtures.Operator.GE,
                }
            )
        assert "ghost" in str(caught.value)
        assert caught.value.field_path == "no_such_field"


class TestConstraints:
    """FR-025 — the half of this that had a fixture and no reader until now."""

    @pytest.mark.parametrize(
        ("reason", "field_type", "constraints"),
        schema_fixtures.INCOMPATIBLE_PAIRINGS,
        ids=[reason for reason, _, _ in schema_fixtures.INCOMPATIBLE_PAIRINGS],
    )
    def test_a_constraint_its_type_cannot_carry_is_refused(
        self, reason: str, field_type, constraints: dict
    ) -> None:
        with pytest.raises(SchemaError) as caught:
            schema_fixtures.constraint_schema(constraints, field_type=field_type)
        assert caught.value.field_path == "probe"
        assert next(iter(constraints)) in str(caught.value)

    @pytest.mark.parametrize("key", sorted(CONSTRAINT_KEYS))
    def test_every_recognised_key_has_a_declaration_that_loads(self, key: str) -> None:
        """A key with no legal declaration would be unusable rather than merely unenforced."""
        field_type, constraints = schema_fixtures.EVERY_CONSTRAINT_KEY[key]
        assert schema_fixtures.constraint_schema(constraints, field_type=field_type)

    def test_a_constraint_on_a_group_is_refused(self) -> None:
        base = schema_fixtures.invoice_schema()
        supplier = next(field for field in base.fields if field.name == "supplier")
        with pytest.raises(SchemaError, match="group"):
            Schema(
                name=base.name,
                version=base.version,
                fields=(supplier.model_copy(update={"constraints": {"max_length": 2}}),),
            )

    def test_a_length_bound_on_a_repeating_group_counts_entries_and_loads(self) -> None:
        base = schema_fixtures.invoice_schema()
        lines = next(field for field in base.fields if field.name == "line_items")
        schema = Schema(
            name=base.name,
            version=base.version,
            fields=(lines.model_copy(update={"constraints": {"min_length": 1}}),),
        )
        assert schema.field_at("line_items").constraints == {"min_length": 1}


class TestMalformedDeclarations:
    """T093 — a constraint whose *value* cannot be evaluated (FR-019, SC-005).

    The key and its type domain were already checked. The value was not, and the
    failure mode was the quiet one: an unparseable `minimum` made the comparison
    impossible, and an impossible comparison reported `passed` for every value —
    the same defect SC-005 prevents for keys, one level in.

    One of these was not quiet. `{"max_length": "abc"}` reached `int()` while a
    document was being checked and escaped as a bare `ValueError`, which is the
    error-model contradiction FR-054 names.
    """

    @pytest.mark.parametrize(
        ("reason", "field_type", "constraints"),
        schema_fixtures.MALFORMED_DECLARATIONS,
        ids=[reason for reason, _, _ in schema_fixtures.MALFORMED_DECLARATIONS],
    )
    def test_it_is_refused_at_load(self, reason: str, field_type, constraints: dict) -> None:
        with pytest.raises(SchemaError) as caught:
            schema_fixtures.constraint_schema(constraints, field_type=field_type)
        assert caught.value.field_path == "probe"
        assert next(iter(constraints)) in str(caught.value)

    def test_an_enum_as_a_string_would_have_rejected_the_value_it_names(self) -> None:
        """The slip that motivates the check, stated as the damage it does.

        `{"enum": "EUR"}` is a missing pair of brackets. Read as a list it becomes
        `['E', 'U', 'R']`, so the schema rejects exactly the value its author
        wrote it to accept — silently, and on every document.
        """
        assert list("EUR") == ["E", "U", "R"]
        with pytest.raises(SchemaError, match="non-empty list"):
            schema_fixtures.constraint_schema({"enum": "EUR"})

    def test_a_single_member_enum_still_loads(self) -> None:
        """The negative half: a one-item list is a legitimate enum, not a slip."""
        schema = schema_fixtures.constraint_schema({"enum": ["EUR"]})
        assert schema.field_at("probe").constraints == {"enum": ["EUR"]}

    def test_no_evaluator_reports_passed_for_a_declaration_it_cannot_read(self) -> None:
        """The property behind the whole list, asserted at the evaluator.

        Reaching an evaluator with an unreadable declaration should be impossible
        after the load-time check — so if the two layers ever disagree, the
        evaluator raises rather than reporting a check that was never made.
        """
        from docdoc.extraction.schema import FieldSpec, FieldType
        from docdoc.validation.constraints import check_constraints
        from docdoc.validation.enumerate import Slot
        from tests.support import make_extracted

        for constraints, value in (
            ({"minimum": "not-a-number"}, Decimal("-5")),
            ({"maximum": None}, Decimal("9999")),
            ({"multiple_of": "abc"}, Decimal("1.23")),
        ):
            field = FieldSpec.model_construct(
                name="probe", type=FieldType.DECIMAL, constraints=constraints
            )
            slot = Slot(path="probe", field=field, value=make_extracted("probe", value=value))
            with pytest.raises(SchemaError):
                check_constraints(slot)

    def test_a_well_formed_declaration_of_every_key_still_loads(self) -> None:
        """SC-005 from the other side: the check must not refuse legitimate schemas."""
        for key, (field_type, constraints) in schema_fixtures.EVERY_CONSTRAINT_KEY.items():
            assert schema_fixtures.constraint_schema(constraints, field_type=field_type), key
