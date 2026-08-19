"""T063 — nothing is repaired, and a failing check does not raise.

Two halves of one boundary. Validation reports; it does not fix. And a finding is
a statement about the document while an error is a statement about the request,
so a document full of errors still *returns* a result — the caller gets a verdict
to act on, not an exception to catch (FR-004, FR-044, SC-006).
"""

from __future__ import annotations

import pytest

from docdoc.validation import Severity, ValidationError, Verdict, validate
from tests.fixtures.validation import artifacts
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation.schemas import invoice_schema
from tests.support import make_extracted


def _snapshot(pair):
    return (
        pair.extraction.model_dump(),
        pair.grounding.model_dump(),
        pair.schema.model_dump(),
    )


def test_a_clean_run_changes_nothing() -> None:
    pair = artifacts.build()
    before = _snapshot(pair)
    validate(pair.extraction, pair.grounding, pair.schema)
    assert _snapshot(pair) == before


def test_a_run_that_produces_findings_changes_nothing() -> None:
    schema = invoice_schema(rules=rule_fixtures.every_kind())
    pair = artifacts.build(
        schema=schema,
        total="1000.00",
        total_claim="1420.00",
        currency="GBP",
        number="INV-1",
        tax_id=None,
    )
    before = _snapshot(pair)
    result = validate(pair.extraction, pair.grounding, schema)
    assert result.counts.failed >= 4
    assert _snapshot(pair) == before


def test_a_refused_run_changes_nothing() -> None:
    pair = artifacts.build()
    other = artifacts.build(number="INV-2026-002")
    before = _snapshot(pair)
    with pytest.raises(ValidationError):
        validate(pair.extraction, other.grounding, pair.schema)
    assert _snapshot(pair) == before


def test_a_value_that_would_pass_after_trimming_still_fails() -> None:
    """The kindest possible correction is still a correction."""
    pair = artifacts.build(currency=" EUR")
    result = validate(pair.extraction, pair.grounding, pair.schema)
    assert result.verdict is Verdict.INVALID
    assert pair.extraction.values["currency"].value == " EUR"


def test_a_number_is_not_rounded_to_make_a_rule_pass() -> None:
    schema = invoice_schema(rules=(rule_fixtures.sum_rule(),))
    pair = artifacts.build(schema=schema, total="1419.999", total_claim="1420.00")
    result = validate(pair.extraction, pair.grounding, schema)
    assert result.verdict is Verdict.INVALID
    assert str(pair.extraction.values["total"].value) == "1419.999"


def test_an_absence_is_not_filled_in() -> None:
    pair = artifacts.build(extraction_overrides={"total": make_extracted("total", present=False)})
    validate(pair.extraction, pair.grounding, pair.schema)
    assert pair.extraction.values["total"].present is False
    assert pair.extraction.values["total"].value is None


def test_a_document_that_fails_everything_still_returns() -> None:
    """FR-044 — an error is about the request; a finding is about the document."""
    schema = invoice_schema(rules=rule_fixtures.every_kind())
    pair = artifacts.build(
        schema=schema,
        number="X",
        currency="GBP",
        total="1.00",
        total_claim="1420.00",
        due="2020-01-01",
        tax_id=None,
    )
    result = validate(pair.extraction, pair.grounding, schema)  # must not raise
    assert result.verdict is Verdict.INVALID
    assert result.findings_at(Severity.ERROR)


def test_an_error_is_never_returned_as_a_finding() -> None:
    pair = artifacts.build()
    other = artifacts.build(number="INV-2026-002")
    with pytest.raises(ValidationError) as caught:
        validate(pair.extraction, other.grounding, pair.schema)
    assert caught.value.expected
    assert caught.value.actual
