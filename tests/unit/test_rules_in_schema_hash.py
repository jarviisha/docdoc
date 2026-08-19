"""T012 — rules are hashed when declared, and invisible when not (FR-053, SC-019).

Two halves pulling opposite ways, which is why both are here. A declared rule
changes what a result *means*, so editing one must invalidate the extraction
artifact. A schema that declares none must hash exactly as it did before this
milestone existed — otherwise introducing rules would invalidate every stored
extraction artifact in the world in exchange for a feature those schemas do not
use.

The second half's real assertion lives in `tests/unit/test_schema_snapshot.py`,
which pins the shipped schemas' hashes and must pass **unedited**. What is here
is the mechanism that makes it pass.
"""

from __future__ import annotations

from decimal import Decimal

from docdoc.extraction import schema_hash_for
from docdoc.extraction.schema import Operator, RuleKind, RuleSpec, Schema
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation import schemas as schema_fixtures


def _with_rules(*rules: RuleSpec) -> Schema:
    base = schema_fixtures.invoice_schema()
    return Schema(name=base.name, version=base.version, fields=base.fields, rules=rules)


def test_declaring_no_rules_leaves_the_hash_where_it_was() -> None:
    """The payload for a rule-less schema is byte-identical to the pre-Milestone-5 one."""
    without = schema_fixtures.invoice_schema()
    assert without.rules == ()
    # An empty tuple is not folded at all, so the hash cannot depend on it.
    assert schema_hash_for(without) == schema_hash_for(_with_rules())


def test_adding_a_rule_moves_the_hash() -> None:
    without = schema_fixtures.invoice_schema()
    assert schema_hash_for(_with_rules(rule_fixtures.sum_rule())) != schema_hash_for(without)


def test_editing_a_tolerance_moves_the_hash() -> None:
    exact = _with_rules(rule_fixtures.sum_rule(tolerance="0"))
    tolerant = _with_rules(rule_fixtures.sum_rule(tolerance="0.01"))
    assert schema_hash_for(exact) != schema_hash_for(tolerant)


def test_editing_a_severity_moves_the_hash() -> None:
    default = _with_rules(rule_fixtures.sum_rule())
    warned = _with_rules(rule_fixtures.sum_rule(severity="warning"))
    assert schema_hash_for(default) != schema_hash_for(warned)


def test_editing_an_operator_moves_the_hash() -> None:
    ge = _with_rules(rule_fixtures.comparison_rule(Operator.GE))
    gt = _with_rules(rule_fixtures.comparison_rule(Operator.GT))
    assert schema_hash_for(ge) != schema_hash_for(gt)


def test_renaming_a_rule_moves_the_hash() -> None:
    """A finding names its rule, so the id is part of what a consumer reads."""
    original = rule_fixtures.sum_rule()
    renamed = original.model_copy(update={"id": "totals_agree"})
    assert schema_hash_for(_with_rules(original)) != schema_hash_for(_with_rules(renamed))


def test_reordering_rules_never_moves_the_hash() -> None:
    """Declaration order is presentation, exactly as it is for fields (EXT-7)."""
    a, b = rule_fixtures.sum_rule(), rule_fixtures.comparison_rule()
    assert schema_hash_for(_with_rules(a, b)) == schema_hash_for(_with_rules(b, a))


def test_a_tolerance_is_hashed_as_a_decimal_not_a_float() -> None:
    """Trailing zeros are not a schema edit; a hair's difference in value is.

    `0.30` and `0.3` are the same tolerance and admit the same invoices, so they
    must hash alike or a trailing zero would invalidate every stored extraction
    artifact for that schema. A tolerance that differs by less than a float can
    represent must still hash differently, which is what forbids a float in this
    payload.
    """
    fine = RuleSpec(
        id="t",
        kind=RuleKind.SUM_EQUALS,
        operands=("line_items.amount", "total"),
        tolerance=Decimal("0.30"),
    )
    same_value = fine.model_copy(update={"tolerance": Decimal("0.1") + Decimal("0.2")})
    assert fine.tolerance == same_value.tolerance
    assert schema_hash_for(_with_rules(fine)) == schema_hash_for(_with_rules(same_value))

    tighter = fine.model_copy(update={"tolerance": Decimal("0.3000000000000000000000001")})
    assert float(tighter.tolerance) == float(fine.tolerance)  # a float cannot tell them apart
    assert schema_hash_for(_with_rules(fine)) != schema_hash_for(_with_rules(tighter))
