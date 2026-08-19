"""T061 — three verdicts, derived mechanically, with no boolean to reach for.

The middle state is the whole point of this file. `incomplete` exists so that a
run where the rules could not be evaluated cannot report the same word as a run
where every rule ran and passed. A consumer that only ever branches on `INVALID`
has opted into unchecked results; the type makes that a choice rather than an
accident.
"""

from __future__ import annotations

from docdoc.validation import Severity, Verdict, validate
from docdoc.validation.record import CheckRecord, failed, not_evaluated, passed
from docdoc.validation.result import CheckKind, Outcome, ReasonCode
from docdoc.validation.verdict import count, derive_verdict
from tests.fixtures.validation import artifacts
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation.schemas import invoice_schema


def _passed() -> CheckRecord:
    return passed("a#required", "a", CheckKind.REQUIRED)


def _failed(severity: Severity) -> CheckRecord:
    return failed(
        "b#pattern",
        "b",
        CheckKind.CONSTRAINT,
        reason=ReasonCode.PATTERN_UNMATCHED,
        severity=severity,
    )


def _skipped() -> CheckRecord:
    return not_evaluated("rule:c@c", "c", CheckKind.RULE, reason=ReasonCode.OPERAND_ABSENT)


class TestDerivation:
    def test_nothing_at_all_is_valid(self) -> None:
        """A zero-field schema asks nothing and gets a boring answer."""
        assert derive_verdict(()) is Verdict.VALID

    def test_all_passed_is_valid(self) -> None:
        assert derive_verdict((_passed(), _passed())) is Verdict.VALID

    def test_an_error_makes_it_invalid(self) -> None:
        assert derive_verdict((_passed(), _failed(Severity.ERROR))) is Verdict.INVALID

    def test_warnings_and_infos_never_reject(self) -> None:
        records = (_passed(), _failed(Severity.WARNING), _failed(Severity.INFO))
        assert derive_verdict(records) is Verdict.VALID

    def test_any_number_of_warnings_still_never_rejects(self) -> None:
        """A threshold on warnings would be routing policy, which is not this stage's."""
        records = tuple(_failed(Severity.WARNING) for _ in range(50))
        assert derive_verdict(records) is Verdict.VALID

    def test_an_unevaluated_check_makes_it_incomplete(self) -> None:
        assert derive_verdict((_passed(), _skipped())) is Verdict.INCOMPLETE

    def test_a_failure_outranks_an_unevaluated_check(self) -> None:
        records = (_failed(Severity.ERROR), _skipped())
        assert derive_verdict(records) is Verdict.INVALID

    def test_a_run_where_everything_was_skipped_is_not_valid(self) -> None:
        """The vacuous pass this milestone exists to make unrepresentable."""
        records = tuple(_skipped() for _ in range(5))
        assert derive_verdict(records) is Verdict.INCOMPLETE


class TestCounts:
    def test_they_reconcile(self) -> None:
        records = (_passed(), _failed(Severity.ERROR), _skipped())
        counts = count(records)
        assert counts.declared == 3
        assert counts.evaluated == 2
        assert counts.passed == 1
        assert counts.failed == 1
        assert counts.not_evaluated == 1

    def test_severities_are_counted_separately_from_outcomes(self) -> None:
        records = (_failed(Severity.ERROR), _failed(Severity.WARNING), _skipped())
        counts = count(records)
        assert counts.errors == 1
        # The skipped check carries WARNING and is counted with the warnings; its
        # effect on the verdict comes from the outcome, not from this number.
        assert counts.warnings == 2


def test_there_is_no_boolean_view_of_the_verdict() -> None:
    """FR-042 — three states do not fit in one bit, and nothing here pretends they do.

    Checked by introspection rather than by reading the class, so that a later
    `is_valid` convenience property fails this test instead of quietly collapsing
    the vocabulary. A convenience like that would be read far more often than the
    verdict itself.
    """
    schema = invoice_schema(rules=rule_fixtures.every_kind())
    pair = artifacts.build(schema=schema)
    result = validate(pair.extraction, pair.grounding, schema)

    inspected = set(type(result).model_fields) | {
        name for name, attribute in vars(type(result)).items() if isinstance(attribute, property)
    }
    for name in sorted(inspected):
        value = getattr(result, name)
        assert not isinstance(value, bool), (
            f"ValidationResult.{name} is a boolean. The verdict has three states, and a "
            "two-state view of it would be the vacuous-pass bug wearing a shorter name"
        )
    assert type(result).__bool__ is object.__bool__ if hasattr(type(result), "__bool__") else True


def test_the_verdict_is_derived_not_settable_by_options() -> None:
    """A configurable verdict would be routing policy (FR-046) under another name."""
    from docdoc.validation import ValidationOptions

    assert set(ValidationOptions.model_fields) == {"grounding_policy", "enabled_rules"}


def test_an_end_to_end_run_reports_each_state() -> None:
    schema = invoice_schema(rules=rule_fixtures.every_kind())

    sound = artifacts.build(schema=schema)
    assert validate(sound.extraction, sound.grounding, schema).verdict is Verdict.VALID

    broken = artifacts.build(schema=schema, total="1000.00", total_claim="1420.00")
    assert validate(broken.extraction, broken.grounding, schema).verdict is Verdict.INVALID

    unchecked = artifacts.build(schema=schema, due=None)
    result = validate(unchecked.extraction, unchecked.grounding, schema)
    assert result.verdict is Verdict.INCOMPLETE
    assert any(check.outcome is Outcome.NOT_EVALUATED for check in result.checks)
