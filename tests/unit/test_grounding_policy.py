"""T060 — evidence is read from Milestone 4's record, never re-decided here.

Two guarantees, and the second is the one that makes a finding worth showing to a
human: the recorded grounding status is used as-is (FR-006), and the location a
finding carries is **copied** from the grounding outcome field by field, so a
recomputed span would fail this file rather than quietly point somewhere else
(FR-038, SC-011).
"""

from __future__ import annotations

from docdoc.grounding.result import GroundingStatus
from docdoc.validation import (
    GroundingPolicy,
    ReasonCode,
    Severity,
    ValidationOptions,
    Verdict,
    validate,
)
from docdoc.validation.result import Outcome
from tests.fixtures.validation import artifacts


def test_a_present_but_ungrounded_value_is_a_warning_by_default() -> None:
    pair = artifacts.build(ungrounded_total=True)
    result = validate(pair.extraction, pair.grounding, pair.schema)

    finding = next(f for f in result.findings if f.field_path == "total")
    assert finding.reason is ReasonCode.VALUE_NOT_GROUNDED
    assert finding.severity is Severity.WARNING
    assert result.verdict is Verdict.VALID  # a warning does not reject a document


def test_a_policy_of_error_rejects_the_document() -> None:
    """A deployment that requires located evidence says so explicitly."""
    pair = artifacts.build(ungrounded_total=True)
    options = ValidationOptions(grounding_policy=GroundingPolicy(ungrounded=Severity.ERROR))
    result = validate(pair.extraction, pair.grounding, pair.schema, options=options)
    assert result.verdict is Verdict.INVALID


def test_an_absent_value_produces_no_grounding_check() -> None:
    """FR-036 — a correctly reported absence is not a failure to locate anything."""
    pair = artifacts.build()  # `notes` is absent
    result = validate(pair.extraction, pair.grounding, pair.schema)
    assert result.check("notes#grounding") is None


def test_an_exactly_grounded_value_produces_no_finding_by_default() -> None:
    pair = artifacts.build()
    result = validate(pair.extraction, pair.grounding, pair.schema)
    assert result.check("number#grounding") is None
    assert not any(f.field_path == "number" for f in result.findings)


def test_exact_coverage_can_be_audited_on_request() -> None:
    pair = artifacts.build()
    options = ValidationOptions(grounding_policy=GroundingPolicy(exact=Severity.INFO))
    result = validate(pair.extraction, pair.grounding, pair.schema, options=options)
    check = result.check("number#grounding")
    assert check is not None
    assert check.outcome is Outcome.PASSED


def test_an_approximate_grounding_is_reported_with_its_score() -> None:
    pair = artifacts.build(number="INV-2026-O01")  # letter O for zero: a near miss
    outcome = pair.grounding.outcomes["number"]
    assert outcome.status is GroundingStatus.FUZZY

    result = validate(pair.extraction, pair.grounding, pair.schema)
    finding = next(
        f for f in result.findings if f.reason is ReasonCode.VALUE_GROUNDED_APPROXIMATELY
    )
    assert finding.severity is Severity.INFO
    assert f"{outcome.score:.4f}" in finding.actual


def test_the_recorded_grounding_is_untouched() -> None:
    """FR-006 — this stage reads the status; it does not get a vote."""
    pair = artifacts.build(ungrounded_total=True)
    before = pair.grounding.model_dump()
    validate(pair.extraction, pair.grounding, pair.schema)
    assert pair.grounding.model_dump() == before


def test_a_finding_carries_the_location_grounding_computed() -> None:
    """SC-011 — equal field by field, not merely non-empty.

    A location that was recomputed rather than copied would still be a plausible
    span; this is what tells the two apart.
    """
    # A value that is genuinely in the document — so grounding locates it — and
    # is nonsense as an invoice number, so the pattern check fails on it.
    pair = artifacts.build(number="Widget A")
    result = validate(pair.extraction, pair.grounding, pair.schema)
    finding = next(f for f in result.findings if f.reason is ReasonCode.PATTERN_UNMATCHED)
    outcome = pair.grounding.outcomes["number"]

    assert outcome.span is not None
    assert finding.span == outcome.span
    assert finding.pages == outcome.pages
    assert finding.geometry == outcome.geometry


def test_a_finding_about_an_unlocated_value_carries_no_location() -> None:
    """`None` rather than a nearby span: no evidence is a fact worth stating."""
    pair = artifacts.build(ungrounded_total=True)
    result = validate(pair.extraction, pair.grounding, pair.schema)
    finding = next(f for f in result.findings if f.reason is ReasonCode.VALUE_NOT_GROUNDED)
    assert finding.span is None
    assert finding.pages == ()


def test_a_policy_of_none_emits_no_check_at_all() -> None:
    """Different from emitting one that passes: there is no obligation to count."""
    pair = artifacts.build(ungrounded_total=True)
    options = ValidationOptions(grounding_policy=GroundingPolicy(ungrounded=None))
    result = validate(pair.extraction, pair.grounding, pair.schema, options=options)
    assert result.check("total#grounding") is None
