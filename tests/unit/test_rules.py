"""T053 — the four rule kinds, passing and failing.

The rule Principle VII names by example (`sum(line_items) == total`) is the one
most of this file is about, and the cases worth reading are the ones where every
individual field is well formed and the document is still wrong.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from docdoc.extraction.schema import Operator, Schema
from docdoc.validation import ReasonCode, Severity, Verdict, validate
from docdoc.validation.result import Outcome
from tests.fixtures.validation import artifacts
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation.schemas import invoice_schema
from tests.support import make_extracted


def _run(rules, **kwargs):
    schema = invoice_schema(rules=rules)
    pair = artifacts.build(schema=schema, **kwargs)
    return validate(pair.extraction, pair.grounding, schema)


class TestSumEquals:
    def test_it_passes_when_the_arithmetic_holds(self) -> None:
        result = _run((rule_fixtures.sum_rule(),))
        assert result.verdict is Verdict.VALID
        assert result.check("rule:total_matches_lines@total").outcome is Outcome.PASSED

    def test_it_fails_when_the_total_is_a_line_short(self) -> None:
        result = _run((rule_fixtures.sum_rule(),), total="1000.00", total_claim="1420.00")
        finding = next(f for f in result.findings if f.reason is ReasonCode.SUM_MISMATCH)
        assert finding.expected == "1420.00"
        assert finding.actual == "1000.00"
        assert result.verdict is Verdict.INVALID

    def test_a_declared_tolerance_absorbs_a_rounding_cent(self) -> None:
        off_by_a_cent = "1419.99"
        assert (
            _run(
                (rule_fixtures.sum_rule(tolerance="0.01"),),
                total=off_by_a_cent,
                total_claim="1420.00",
            ).verdict
            is Verdict.VALID
        )
        assert (
            _run((rule_fixtures.sum_rule(),), total=off_by_a_cent, total_claim="1420.00").verdict
            is Verdict.INVALID
        )

    def test_a_tolerance_larger_than_the_difference_is_still_a_bound(self) -> None:
        assert (
            _run(
                (rule_fixtures.sum_rule(tolerance="500"),), total="1000.00", total_claim="1420.00"
            ).verdict
            is Verdict.VALID
        )

    def test_an_empty_group_sums_to_zero_and_the_rule_is_evaluated(self) -> None:
        """FR-031 — the sum of no entries is a defined quantity, unlike a missing one."""
        schema = invoice_schema(rules=(rule_fixtures.sum_rule(),))
        pair = artifacts.build(schema=schema)
        values = dict(pair.extraction.values)
        values["line_items"] = ()
        extraction = pair.extraction.model_copy(update={"values": values})
        result = validate(extraction, pair.grounding, schema)

        check = result.check("rule:total_matches_lines@total")
        assert check.outcome is Outcome.FAILED  # a total of 1420.00 over no lines
        assert result.verdict is Verdict.INVALID

    def test_the_finding_names_every_line_that_fed_the_sum(self) -> None:
        """FR-032 — naming only the total would hide half the evidence."""
        result = _run((rule_fixtures.sum_rule(),), total="1000.00", total_claim="1420.00")
        finding = next(f for f in result.findings if f.reason is ReasonCode.SUM_MISMATCH)
        assert finding.participants == (
            "total",
            "line_items[0].amount",
            "line_items[1].amount",
        )

    def test_it_scales_to_a_long_group(self) -> None:
        schema = invoice_schema(rules=(rule_fixtures.sum_rule(),))
        pair = artifacts.build(schema=schema)
        line = pair.extraction.values["line_items"][1]  # 420.00
        values = dict(pair.extraction.values)
        values["line_items"] = tuple(line for _ in range(200))
        values["total"] = make_extracted("total", value=Decimal("84000.00"), claimed_text="1420.00")
        extraction = pair.extraction.model_copy(update={"values": values})
        result = validate(extraction, pair.grounding, schema)
        assert result.check("rule:total_matches_lines@total").outcome is Outcome.PASSED


class TestProductEquals:
    def test_one_check_per_entry(self) -> None:
        result = _run((rule_fixtures.product_rule(),))
        ids = [c.check_id for c in result.checks if c.check_id.startswith("rule:line_amount")]
        assert ids == [
            "rule:line_amount_is_quantity_times_price@line_items[0].quantity",
            "rule:line_amount_is_quantity_times_price@line_items[1].quantity",
        ]

    def test_it_fails_on_the_entry_that_is_wrong(self) -> None:
        schema = invoice_schema(rules=(rule_fixtures.product_rule(),))
        pair = artifacts.build(schema=schema)
        lines = list(pair.extraction.values["line_items"])
        lines[0] = dict(lines[0])
        lines[0]["quantity"] = make_extracted("line_items[0].quantity", value=3, claimed_text="2")
        values = dict(pair.extraction.values)
        values["line_items"] = tuple(lines)
        result = validate(
            pair.extraction.model_copy(update={"values": values}), pair.grounding, schema
        )
        failed = [f for f in result.findings if f.reason is ReasonCode.PRODUCT_MISMATCH]
        assert [f.field_path for f in failed] == ["line_items[0].quantity"]
        assert failed[0].expected == "1500.00"


class TestComparison:
    def test_a_satisfied_ordering_passes(self) -> None:
        assert _run((rule_fixtures.comparison_rule(),)).verdict is Verdict.VALID

    def test_a_violated_ordering_fails(self) -> None:
        result = _run((rule_fixtures.comparison_rule(),), due="2026-01-01")
        finding = next(f for f in result.findings if f.reason is ReasonCode.COMPARISON_FAILED)
        assert finding.field_path == "due_date"
        assert "issue_date" in finding.participants

    @pytest.mark.parametrize(
        ("operator", "expected"),
        [
            (Operator.GE, Verdict.VALID),
            (Operator.GT, Verdict.VALID),
            (Operator.LT, Verdict.INVALID),
            (Operator.EQ, Verdict.INVALID),
            (Operator.NE, Verdict.VALID),
        ],
    )
    def test_each_operator(self, operator: Operator, expected: Verdict) -> None:
        assert _run((rule_fixtures.comparison_rule(operator),)).verdict is expected

    def test_numbers_compare_as_exact_decimals(self) -> None:
        from docdoc.extraction.schema import RuleKind, RuleSpec

        rule = RuleSpec(
            id="lines_do_not_exceed_total",
            kind=RuleKind.COMPARISON,
            operands=("line_items.amount", "line_items.unit_price"),
            operator=Operator.GE,
        )
        assert _run((rule,)).verdict is Verdict.VALID


class TestConditionalPresence:
    def test_it_passes_when_the_companion_is_there(self) -> None:
        assert _run((rule_fixtures.presence_rule(),)).verdict is Verdict.VALID

    def test_it_fails_when_the_trigger_is_present_and_the_companion_is_not(self) -> None:
        result = _run((rule_fixtures.presence_rule(),), tax_id=None)
        finding = next(f for f in result.findings if f.reason is ReasonCode.COMPANION_MISSING)
        assert finding.participants == ("supplier.name", "supplier.tax_id")

    def test_a_false_antecedent_is_a_pass_not_a_skip(self) -> None:
        """The rule ran; it simply did not fire. That is not an unchecked obligation."""
        schema = invoice_schema(rules=(rule_fixtures.presence_rule(),))
        pair = artifacts.build(schema=schema, tax_id=None)
        values = dict(pair.extraction.values)
        values["supplier"] = {
            "name": make_extracted("supplier.name", present=False),
            "tax_id": make_extracted("supplier.tax_id", present=False),
        }
        result = validate(
            pair.extraction.model_copy(update={"values": values}), pair.grounding, schema
        )
        assert (
            result.check("rule:named_supplier_has_tax_id@supplier.name").outcome is Outcome.PASSED
        )
        assert result.verdict is Verdict.VALID


def test_a_disabled_rule_produces_no_check() -> None:
    from docdoc.validation import ValidationOptions

    schema = invoice_schema(rules=rule_fixtures.every_kind())
    pair = artifacts.build(schema=schema, total="1000.00", total_claim="1420.00")
    options = ValidationOptions(enabled_rules=frozenset({"due_after_issue"}))
    result = validate(pair.extraction, pair.grounding, schema, options=options)

    assert result.provenance.enabled_rules == ("due_after_issue",)
    assert not any(check.check_id.startswith("rule:total_matches") for check in result.checks)
    assert result.verdict is Verdict.VALID  # the sum rule was not run


def test_enabling_a_rule_the_schema_does_not_declare_is_refused() -> None:
    """A typo must not silently disable a rule, which is indistinguishable from success."""
    from docdoc.validation import ValidationError, ValidationOptions

    schema = invoice_schema(rules=(rule_fixtures.sum_rule(),))
    pair = artifacts.build(schema=schema)
    with pytest.raises(ValidationError, match="total_matchez"):
        validate(
            pair.extraction,
            pair.grounding,
            schema,
            options=ValidationOptions(enabled_rules=frozenset({"total_matchez"})),
        )


def test_an_unknown_kind_cannot_exist() -> None:
    """The vocabulary is closed at the type level, not by a runtime check."""
    from docdoc.extraction.schema import RuleKind

    with pytest.raises(ValueError, match="regex_of_the_day"):
        RuleKind("regex_of_the_day")


def test_a_schema_with_no_rules_still_validates() -> None:
    schema = Schema(name="bare", version=1)
    pair = artifacts.build(schema=invoice_schema())
    result = validate(pair.extraction, pair.grounding, pair.schema)
    assert result.provenance.enabled_rules == ()
    assert schema.rules == ()


class TestSeverityOverride:
    """T105 — FR-040, the branch of the severity logic no fixture reached.

    A convergence pass found this by mutation rather than by reading: replacing
    `_severity()` with a hardcoded `Severity.ERROR` — deleting the author's
    override entirely — left all 1722 tests green. `test_rules_in_schema_hash.py`
    asserts the override moves `schema_hash`, which is about identity and says
    nothing about behaviour.

    The assertions below are on the **verdict**, not only on the finding. The
    override's whole purpose is to decide whether a document is rejected, so a
    test that checked the severity field alone would still pass if the verdict
    derivation stopped reading it.
    """

    @staticmethod
    def _failing(severity: str | None):
        schema = invoice_schema(rules=(rule_fixtures.sum_rule(severity=severity),))
        pair = artifacts.build(schema=schema, total="1000.00", total_claim="1420.00")
        result = validate(pair.extraction, pair.grounding, schema)
        finding = next(f for f in result.findings if f.rule_id == "total_matches_lines")
        return result, finding

    def test_the_default_rejects_the_document(self) -> None:
        result, finding = self._failing(None)
        assert finding.severity is Severity.ERROR
        assert result.verdict is Verdict.INVALID
        assert result.counts.errors == 1

    def test_an_author_can_make_the_same_failure_a_warning(self) -> None:
        """The same document, the same broken arithmetic, a different verdict."""
        result, finding = self._failing("warning")
        assert finding.severity is Severity.WARNING
        assert result.verdict is Verdict.VALID
        assert result.counts.errors == 0
        assert result.counts.warnings == 1

    def test_an_author_can_make_it_informational(self) -> None:
        result, finding = self._failing("info")
        assert finding.severity is Severity.INFO
        assert result.verdict is Verdict.VALID
        assert result.counts.infos == 1

    def test_the_check_still_failed_whatever_the_severity(self) -> None:
        """An override changes what a failure *means*, never whether it happened.

        A warning-severity rule that quietly reported `passed` would hide the
        finding from `checks` as well, and the counts would stop reconciling with
        what the document actually did.
        """
        for severity in (None, "warning", "info"):
            result, _ = self._failing(severity)
            assert result.check("rule:total_matches_lines@total").outcome is Outcome.FAILED
            assert result.counts.failed == 1

    def test_the_override_does_not_leak_to_other_check_kinds(self) -> None:
        """VAL-11 — a rule's severity is the author's; requiredness is not."""
        schema = invoice_schema(rules=(rule_fixtures.sum_rule(severity="warning"),))
        pair = artifacts.build(
            schema=schema,
            total="1000.00",
            total_claim="1420.00",
            extraction_overrides={"currency": make_extracted("currency", present=False)},
        )
        result = validate(pair.extraction, pair.grounding, schema)
        required = next(f for f in result.findings if f.field_path == "currency")
        assert required.severity is Severity.ERROR
        assert result.verdict is Verdict.INVALID  # the required field, not the rule


def test_disabling_every_rule_is_not_the_same_as_declaring_none() -> None:
    """T107, VAL-26 — the empty set a deployment reaches when it turns everything off.

    `enabled_rules=None` runs them all; `frozenset()` runs none. Both produce a
    result, and the difference has to be visible in the provenance and in the
    artifact id — otherwise "we ran no rules" is indistinguishable from "this
    schema declares none", which is the silent-omission VAL-26 forbids.
    """
    from docdoc.validation import ValidationOptions

    schema = invoice_schema(rules=rule_fixtures.every_kind())
    pair = artifacts.build(schema=schema, total="1000.00", total_claim="1420.00")

    everything = validate(pair.extraction, pair.grounding, schema)
    nothing = validate(
        pair.extraction,
        pair.grounding,
        schema,
        options=ValidationOptions(enabled_rules=frozenset()),
    )

    assert everything.verdict is Verdict.INVALID
    assert nothing.verdict is Verdict.VALID
    assert nothing.provenance.enabled_rules == ()
    assert not any(check.check_id.startswith("rule:") for check in nothing.checks)
    assert nothing.artifact_id != everything.artifact_id
