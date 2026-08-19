"""T010 — a schema whose rule or constraint cannot work is refused at load.

The point is not that bad input raises. It is *when*: at load, before any result
is validated. A rule or a constraint that reaches a validation run and cannot be
evaluated becomes a check that never ran, and a check that never ran is exactly
what this milestone exists to make impossible (FR-025, FR-029, FR-056, SC-014).
"""

from __future__ import annotations

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
