"""One well-formed declaration per rule kind, and the ones that must fail at load.

The invalid half matters as much as the valid half: a rule that cannot work must
be refused when the schema loads (FR-056), because the alternative is a rule that
reaches a validation run and quietly becomes a check nobody notices did not run.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from docdoc.extraction.schema import Operator, RuleKind, RuleSpec

__all__ = [
    "INVALID_RULES",
    "comparison_rule",
    "every_kind",
    "presence_rule",
    "product_rule",
    "sum_rule",
]


def sum_rule(*, tolerance: str = "0", severity: str | None = None) -> RuleSpec:
    """`sum(line_items[].amount) == total` — the rule Principle VII names by example."""
    return RuleSpec(
        id="total_matches_lines",
        kind=RuleKind.SUM_EQUALS,
        operands=("line_items.amount", "total"),
        tolerance=Decimal(tolerance),
        severity=severity,
    )


def product_rule(*, tolerance: str = "0") -> RuleSpec:
    """`quantity * unit_price == amount`, once per line."""
    return RuleSpec(
        id="line_amount_is_quantity_times_price",
        kind=RuleKind.PRODUCT_EQUALS,
        operands=("line_items.quantity", "line_items.unit_price", "line_items.amount"),
        tolerance=Decimal(tolerance),
    )


def comparison_rule(operator: Operator = Operator.GE) -> RuleSpec:
    """`due_date >= issue_date` — an ordering a document can violate while every field parses."""
    return RuleSpec(
        id="due_after_issue",
        kind=RuleKind.COMPARISON,
        operands=("due_date", "issue_date"),
        operator=operator,
    )


def presence_rule() -> RuleSpec:
    """If a supplier name is there, so must its tax id be."""
    return RuleSpec(
        id="named_supplier_has_tax_id",
        kind=RuleKind.CONDITIONAL_PRESENCE,
        operands=("supplier.name", "supplier.tax_id"),
    )


def every_kind() -> tuple[RuleSpec, ...]:
    return (sum_rule(), product_rule(), comparison_rule(), presence_rule())


#: `(reason, kwargs)` pairs that must raise when the schema is constructed.
#:
#: The reason is carried so a failure reads as "the schema layer stopped
#: accepting a rule it should accept" rather than as an opaque parametrised id.
INVALID_RULES: tuple[tuple[str, dict[str, Any]], ...] = (
    (
        "an operand this schema does not declare",
        {
            "id": "ghost",
            "kind": RuleKind.COMPARISON,
            "operands": ("due_date", "no_such_field"),
            "operator": Operator.GE,
        },
    ),
    (
        "a comparison across declared types",
        {
            "id": "mixed",
            "kind": RuleKind.COMPARISON,
            "operands": ("due_date", "total"),
            "operator": Operator.GE,
        },
    ),
    (
        "a sum over something that is not in a repeating group",
        {"id": "flat_sum", "kind": RuleKind.SUM_EQUALS, "operands": ("total", "total")},
    ),
    (
        "a total that is itself inside the group being summed",
        {
            "id": "inner_total",
            "kind": RuleKind.SUM_EQUALS,
            "operands": ("line_items.amount", "line_items.unit_price"),
        },
    ),
    (
        "a per-entry product whose operands span two scopes",
        {
            "id": "split_product",
            "kind": RuleKind.PRODUCT_EQUALS,
            "operands": ("line_items.quantity", "line_items.unit_price", "total"),
        },
    ),
    (
        "a comparison between a line field and a document field",
        {
            "id": "cross_scope",
            "kind": RuleKind.COMPARISON,
            "operands": ("line_items.amount", "total"),
            "operator": Operator.LE,
        },
    ),
    (
        "arithmetic on a field that is not numeric",
        {
            "id": "text_sum",
            "kind": RuleKind.SUM_EQUALS,
            "operands": ("line_items.description", "total"),
        },
    ),
    (
        "an operand that names a group rather than a value",
        {
            "id": "group_operand",
            "kind": RuleKind.CONDITIONAL_PRESENCE,
            "operands": ("supplier", "total"),
        },
    ),
    (
        "a comparison with no operator",
        {"id": "no_operator", "kind": RuleKind.COMPARISON, "operands": ("due_date", "issue_date")},
    ),
    (
        "an operator on a kind that compares nothing",
        {
            "id": "stray_operator",
            "kind": RuleKind.CONDITIONAL_PRESENCE,
            "operands": ("supplier.name", "supplier.tax_id"),
            "operator": Operator.EQ,
        },
    ),
    (
        "the wrong number of operands",
        {
            "id": "short_product",
            "kind": RuleKind.PRODUCT_EQUALS,
            "operands": ("line_items.quantity", "line_items.amount"),
        },
    ),
    (
        "a negative tolerance",
        {
            "id": "negative_tolerance",
            "kind": RuleKind.SUM_EQUALS,
            "operands": ("line_items.amount", "total"),
            "tolerance": Decimal("-0.01"),
        },
    ),
    (
        "a tolerance on a kind that compares nothing numeric",
        {
            "id": "tolerant_presence",
            "kind": RuleKind.CONDITIONAL_PRESENCE,
            "operands": ("supplier.name", "supplier.tax_id"),
            "tolerance": Decimal("0.5"),
        },
    ),
    (
        "an unrecognised severity",
        {
            "id": "loud",
            "kind": RuleKind.SUM_EQUALS,
            "operands": ("line_items.amount", "total"),
            "severity": "critical",
        },
    ),
)
