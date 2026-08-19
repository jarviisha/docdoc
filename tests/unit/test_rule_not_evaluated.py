"""T054 — a rule that could not run says so, and is never counted as a pass.

This is the file that stops the most dangerous silent failure this stage has. A
sum rule whose line is missing an amount must **not** treat the absence as zero:
doing so turns "we could not check this invoice" into "this invoice adds up",
which is a wrong answer wearing the costume of a right one (FR-031, FR-010).
"""

from __future__ import annotations

from decimal import Decimal

from docdoc.validation import ReasonCode, Verdict, validate
from docdoc.validation.result import Outcome
from tests.fixtures.validation import artifacts
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation.schemas import invoice_schema
from tests.support import make_extracted


def _without_second_line_amount():
    schema = invoice_schema(rules=(rule_fixtures.sum_rule(),))
    pair = artifacts.build(schema=schema)
    lines = list(pair.extraction.values["line_items"])
    lines[1] = dict(lines[1])
    lines[1]["amount"] = make_extracted("line_items[1].amount", present=False)
    values = dict(pair.extraction.values)
    values["line_items"] = tuple(lines)
    return pair, schema, pair.extraction.model_copy(update={"values": values})


def test_a_missing_amount_is_not_summed_as_zero() -> None:
    pair, schema, extraction = _without_second_line_amount()
    result = validate(extraction, pair.grounding, schema)

    check = result.check("rule:total_matches_lines@total")
    assert check.outcome is Outcome.NOT_EVALUATED
    assert check.reason is ReasonCode.OPERAND_ABSENT

    # If the missing amount had been read as zero, the lines would sum to 1000.00
    # against a stated 1420.00 and this would be a *failed* check instead — a
    # different, and wrong, statement about the document.
    assert not any(f.reason is ReasonCode.SUM_MISMATCH for f in result.findings)


def test_the_finding_names_the_operand_that_was_missing() -> None:
    pair, schema, extraction = _without_second_line_amount()
    result = validate(extraction, pair.grounding, schema)
    finding = next(f for f in result.findings if f.check_id.startswith("rule:total_matches"))
    assert "line_items[1].amount" in finding.message
    assert "line_items[1].amount" in finding.participants


def test_a_failure_outranks_an_unevaluated_check() -> None:
    """The missing amount is *also* a required field, so this result is `invalid`.

    Precedence is deliberate (FR-041): a document that broke a rule is rejected
    whether or not the rest could be audited. `incomplete` is for the case where
    nothing failed and something went unchecked — which the next test covers, on
    an optional field.
    """
    pair, schema, extraction = _without_second_line_amount()
    result = validate(extraction, pair.grounding, schema)
    assert result.verdict is Verdict.INVALID
    assert result.counts.not_evaluated == 1
    assert result.counts.failed == 1


def test_an_absent_comparison_operand_is_not_evaluated() -> None:
    schema = invoice_schema(rules=(rule_fixtures.comparison_rule(),))
    pair = artifacts.build(schema=schema, due=None)
    result = validate(pair.extraction, pair.grounding, schema)

    check = result.check("rule:due_after_issue@due_date")
    assert check.outcome is Outcome.NOT_EVALUATED
    assert check.reason is ReasonCode.OPERAND_ABSENT
    assert result.verdict is Verdict.INCOMPLETE


def test_an_absent_product_operand_is_not_evaluated_for_that_entry_only() -> None:
    schema = invoice_schema(rules=(rule_fixtures.product_rule(),))
    pair = artifacts.build(schema=schema)
    lines = list(pair.extraction.values["line_items"])
    lines[0] = dict(lines[0])
    lines[0]["unit_price"] = make_extracted("line_items[0].unit_price", present=False)
    values = dict(pair.extraction.values)
    values["line_items"] = tuple(lines)
    result = validate(pair.extraction.model_copy(update={"values": values}), pair.grounding, schema)

    first = result.check("rule:line_amount_is_quantity_times_price@line_items[0].quantity")
    second = result.check("rule:line_amount_is_quantity_times_price@line_items[1].quantity")
    assert first.outcome is Outcome.NOT_EVALUATED
    assert second.outcome is Outcome.PASSED


def test_a_not_evaluated_check_is_never_reported_as_passed() -> None:
    """FR-010, stated as the property rather than as a case."""
    pair, schema, extraction = _without_second_line_amount()
    result = validate(extraction, pair.grounding, schema)
    skipped = [c for c in result.checks if c.outcome is Outcome.NOT_EVALUATED]
    assert skipped
    for check in skipped:
        assert check.reason is not None
        assert check.outcome is not Outcome.PASSED


def test_a_type_the_load_time_checks_could_not_see_is_not_evaluated() -> None:
    """A value that parsed as its declared type but cannot do arithmetic.

    Reachable only when an extraction result and a schema disagree in a way the
    hash check does not catch — a hand-built result, in practice. It is here
    because "cannot happen" is the assumption that produces the crash that
    happens.
    """
    schema = invoice_schema(rules=(rule_fixtures.sum_rule(),))
    pair = artifacts.build(schema=schema)
    values = dict(pair.extraction.values)
    values["total"] = make_extracted("total", value="not a number", claimed_text="1420.00")
    result = validate(pair.extraction.model_copy(update={"values": values}), pair.grounding, schema)
    check = result.check("rule:total_matches_lines@total")
    assert check.outcome is Outcome.NOT_EVALUATED
    assert check.reason is ReasonCode.TYPE_MISMATCH


def test_counts_reconcile_when_checks_are_skipped() -> None:
    pair, schema, extraction = _without_second_line_amount()
    counts = validate(extraction, pair.grounding, schema).counts
    assert counts.declared == counts.passed + counts.failed + counts.not_evaluated
    assert counts.evaluated == counts.passed + counts.failed


def test_a_present_zero_is_not_an_absence() -> None:
    """A line that really is zero is summed, because it was reported."""
    schema = invoice_schema(rules=(rule_fixtures.sum_rule(),))
    pair = artifacts.build(schema=schema, total="1000.00", total_claim="1420.00")
    lines = list(pair.extraction.values["line_items"])
    lines[1] = dict(lines[1])
    lines[1]["amount"] = make_extracted(
        "line_items[1].amount", value=Decimal("0.00"), claimed_text="420.00"
    )
    values = dict(pair.extraction.values)
    values["line_items"] = tuple(lines)
    result = validate(pair.extraction.model_copy(update={"values": values}), pair.grounding, schema)
    assert result.check("rule:total_matches_lines@total").outcome is Outcome.PASSED
