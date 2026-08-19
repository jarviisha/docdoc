"""T037, T055 — extract → ground → validate, end to end, offline.

No credentials, no network, no database, no object storage. The document is
hand-typed, the extraction is constructed, and the grounding comes from the real
grounder — so the locations these findings carry are the ones the grounding stage
actually computed.

The two classes are the independent tests US1 and US2 name.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from tests.fixtures.validation import artifacts
from tests.fixtures.validation import rules as rule_fixtures
from tests.fixtures.validation.schemas import invoice_schema
from tests.support import make_extracted

from docdoc.validation import ReasonCode, Severity, Verdict, validate
from docdoc.validation.result import Outcome


class TestStructural:
    """US1 — every hand-listed violation appears once, and nothing else appears."""

    @pytest.fixture
    def broken(self):
        schema = invoice_schema()
        pair = artifacts.build(
            schema=schema,
            number="INV-26-1",  # fails the pattern
            currency="GBP",  # not in the enum
            extraction_overrides={
                "total": make_extracted("total", present=False),  # required, absent
                "notes": make_extracted(
                    "notes",
                    value="x" * 41,  # one over max_length
                    claimed_text="Paid in full.",
                ),
            },
        )
        return pair, validate(pair.extraction, pair.grounding, schema)

    def test_each_violation_appears_exactly_once(self, broken) -> None:
        _pair, result = broken
        errors = {
            (finding.field_path, finding.reason) for finding in result.findings_at(Severity.ERROR)
        }
        assert errors == {
            ("number", ReasonCode.PATTERN_UNMATCHED),
            ("currency", ReasonCode.NOT_IN_ENUM),
            ("total", ReasonCode.REQUIRED_VALUE_MISSING),
            ("notes", ReasonCode.TOO_LONG),
        }

    def test_a_conforming_value_produces_no_finding(self, broken) -> None:
        _pair, result = broken
        assert not any(f.field_path.startswith("line_items") for f in result.findings)

    def test_the_checks_that_passed_are_still_recorded(self, broken) -> None:
        _pair, result = broken
        for check_id in (
            "issue_date#required",
            "line_items[0].description#required",
            "line_items[1].amount#required",
        ):
            assert result.check(check_id).outcome is Outcome.PASSED

    def test_a_sound_document_is_valid(self) -> None:
        pair = artifacts.build()
        result = validate(pair.extraction, pair.grounding, pair.schema)
        assert result.verdict is Verdict.VALID
        assert result.findings == ()
        assert result.counts.declared == result.counts.passed

    def test_it_runs_with_no_credentials_and_no_network(self, broken) -> None:
        """Asserted structurally: this layer cannot import a client at all."""
        import pathlib

        for path in sorted(pathlib.Path("src/docdoc/validation").rglob("*.py")):
            source = path.read_text(encoding="utf-8")
            for forbidden in ("httpx", "requests", "socket", "urllib"):
                assert f"import {forbidden}" not in source


class TestArithmetic:
    """US2 — the invoice where every field is fine and the total is not."""

    def test_the_sound_document_passes(self) -> None:
        schema = invoice_schema(rules=rule_fixtures.every_kind())
        pair = artifacts.build(schema=schema)
        assert validate(pair.extraction, pair.grounding, schema).verdict is Verdict.VALID

    def test_a_line_short_total_is_reported_with_the_difference(self) -> None:
        schema = invoice_schema(rules=(rule_fixtures.sum_rule(),))
        pair = artifacts.build(schema=schema, total="1000.00", total_claim="1420.00")
        result = validate(pair.extraction, pair.grounding, schema)

        finding = next(f for f in result.findings if f.reason is ReasonCode.SUM_MISMATCH)
        assert finding.expected == "1420.00"
        assert finding.actual == "1000.00"
        assert "420.00" in finding.message  # the difference
        assert result.verdict is Verdict.INVALID

    def test_the_finding_points_at_the_document(self) -> None:
        """The product's whole claim, in one assertion."""
        schema = invoice_schema(rules=(rule_fixtures.sum_rule(),))
        pair = artifacts.build(schema=schema, total="1000.00", total_claim="1420.00")
        result = validate(pair.extraction, pair.grounding, schema)
        finding = next(f for f in result.findings if f.reason is ReasonCode.SUM_MISMATCH)

        assert finding.span is not None
        assert finding.pages == (0,)
        assert pair.document.text[finding.span.start : finding.span.end] == "1420.00"

    def test_a_rounding_cent_is_absorbed_only_where_declared(self) -> None:
        strict = invoice_schema(rules=(rule_fixtures.sum_rule(),))
        lenient = invoice_schema(rules=(rule_fixtures.sum_rule(tolerance="0.01"),))
        for schema, expected in ((strict, Verdict.INVALID), (lenient, Verdict.VALID)):
            pair = artifacts.build(schema=schema, total="1419.99", total_claim="1420.00")
            assert validate(pair.extraction, pair.grounding, schema).verdict is expected

    def test_a_missing_amount_is_reported_as_unchecked_not_as_zero(self) -> None:
        schema = invoice_schema(rules=(rule_fixtures.sum_rule(),))
        pair = artifacts.build(schema=schema)
        lines = [dict(line) for line in pair.extraction.values["line_items"]]
        lines[1]["amount"] = make_extracted("line_items[1].amount", present=False)
        values = dict(pair.extraction.values)
        values["line_items"] = tuple(lines)
        result = validate(
            pair.extraction.model_copy(update={"values": values}), pair.grounding, schema
        )

        check = result.check("rule:total_matches_lines@total")
        assert check.outcome is Outcome.NOT_EVALUATED
        assert check.reason is ReasonCode.OPERAND_ABSENT

    def test_arithmetic_is_exact(self) -> None:
        """A total written with a different scale is the same total."""
        schema = invoice_schema(rules=(rule_fixtures.sum_rule(),))
        pair = artifacts.build(schema=schema, total="1420.0000")
        assert pair.extraction.values["total"].value == Decimal("1420.0000")
        assert validate(pair.extraction, pair.grounding, schema).verdict is Verdict.VALID
