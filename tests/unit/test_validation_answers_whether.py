"""T065 — the mirror of Milestone 4's `test_no_validation_judgment.py`.

That test pins the boundary from the grounding side: a value of 1240.00 whose
claim resolves to text reading 1,420.00 produces a normal grounded outcome, no
finding, no warning, no status change. Grounding answers *where*.

This test pins the same case from the validation side: with a rule declared, the
identical result is reported as failing. Validation answers *whether*.

Together they make the stage boundary mechanical rather than a matter of
discipline. Either test alone would let the boundary drift — the first by
tempting grounding to judge, the second by letting validation assume grounding
already had.
"""

from __future__ import annotations

from decimal import Decimal

from docdoc.grounding.result import GroundingStatus
from docdoc.validation import ReasonCode, Verdict, validate
from tests.fixtures.validation import artifacts
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation.schemas import invoice_schema
from tests.support import make_extracted


def _disagreeing_pair(rules=()):
    """A total of 1240.00 whose claim resolves to the document's 1420.00."""
    schema = invoice_schema(rules=rules)
    return artifacts.build(schema=schema, total="1240.00", total_claim="1420.00"), schema


def test_grounding_saw_no_problem_with_it() -> None:
    """Restating Milestone 4's half here, so this file documents the whole boundary."""
    pair, _ = _disagreeing_pair()
    outcome = pair.grounding.outcomes["total"]
    assert outcome.status is GroundingStatus.EXACT
    assert outcome.span is not None
    assert pair.extraction.values["total"].value == Decimal("1240.00")


def test_without_a_rule_validation_says_nothing_either() -> None:
    """No declared obligation, no finding. This stage judges the schema's rules.

    Worth stating: docdoc does not compare a value against the text it came from
    on its own initiative. What it checks is what a schema author declared.
    """
    pair, schema = _disagreeing_pair()
    result = validate(pair.extraction, pair.grounding, schema)
    assert result.verdict is Verdict.VALID
    assert not any(f.field_path == "total" for f in result.findings)


def test_with_the_rule_declared_it_is_a_finding_here() -> None:
    pair, schema = _disagreeing_pair(rules=(rule_fixtures.sum_rule(),))
    result = validate(pair.extraction, pair.grounding, schema)

    finding = next(f for f in result.findings if f.reason is ReasonCode.SUM_MISMATCH)
    assert finding.field_path == "total"
    assert finding.actual == "1240.00"
    assert finding.expected == "1420.00"
    assert result.verdict is Verdict.INVALID


def test_the_finding_points_at_the_page_grounding_found() -> None:
    """The whole product in one assertion: a rejected value a reviewer can be shown."""
    pair, schema = _disagreeing_pair(rules=(rule_fixtures.sum_rule(),))
    result = validate(pair.extraction, pair.grounding, schema)
    finding = next(f for f in result.findings if f.reason is ReasonCode.SUM_MISMATCH)

    outcome = pair.grounding.outcomes["total"]
    assert finding.span == outcome.span
    assert finding.pages == outcome.pages == (0,)
    assert pair.document.text[finding.span.start : finding.span.end] == "1420.00"


def test_a_constraint_can_catch_it_too() -> None:
    """The same disagreement, caught by a bound rather than by arithmetic."""
    from docdoc.extraction.schema import Schema

    base = invoice_schema()
    bounded = tuple(
        field.model_copy(update={"constraints": {"minimum": "1400.00"}})
        if field.name == "total"
        else field
        for field in base.fields
    )
    schema = Schema(name=base.name, version=base.version, fields=bounded)
    pair = artifacts.build(schema=schema, total="1240.00", total_claim="1420.00")
    result = validate(pair.extraction, pair.grounding, schema)
    assert any(f.reason is ReasonCode.BELOW_MINIMUM for f in result.findings)


def test_the_extracted_value_is_not_replaced_with_the_text() -> None:
    """The obvious 'fix' — take the number from the document — is a repair (FR-004)."""
    pair, schema = _disagreeing_pair(rules=(rule_fixtures.sum_rule(),))
    validate(pair.extraction, pair.grounding, schema)
    assert pair.extraction.values["total"].value == Decimal("1240.00")


def test_the_same_disagreement_in_a_line_item() -> None:
    pair, schema = _disagreeing_pair(rules=(rule_fixtures.product_rule(),))
    lines = list(pair.extraction.values["line_items"])
    lines[0] = dict(lines[0])
    lines[0]["amount"] = make_extracted(
        "line_items[0].amount", value=Decimal("900.00"), claimed_text="1000.00"
    )
    values = dict(pair.extraction.values)
    values["line_items"] = tuple(lines)
    result = validate(pair.extraction.model_copy(update={"values": values}), pair.grounding, schema)
    assert any(f.reason is ReasonCode.PRODUCT_MISMATCH for f in result.findings)
