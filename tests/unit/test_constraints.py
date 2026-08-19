"""T035 — the eight constraint keys Milestone 3 declared and never applied.

The exactness cases are the ones worth reading. A value that differs from an
enum member only in case, or only by surrounding whitespace, **fails**: trimming
or folding it to make it pass would be a silent correction, and FR-004 forbids
one even in the service of a kinder verdict.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest

from docdoc.extraction.schema import FieldType
from docdoc.validation import ReasonCode, Verdict, validate
from docdoc.validation.constraints import check_constraints
from docdoc.validation.enumerate import Slot
from docdoc.validation.result import Outcome
from tests.fixtures.validation import artifacts
from tests.fixtures.validation.schemas import constraint_schema
from tests.support import make_extracted


def _check(constraints: dict[str, Any], value: Any, field_type: FieldType) -> list:
    schema = constraint_schema(constraints, field_type=field_type)
    slot = Slot(
        path="probe",
        field=schema.fields[0],
        value=make_extracted("probe", value=value),
    )
    return check_constraints(slot)


def _passes(constraints: dict[str, Any], value: Any, field_type: FieldType) -> bool:
    checks = _check(constraints, value, field_type)
    assert checks, "a declared constraint must produce a check"
    return all(check.outcome is Outcome.PASSED for check in checks)


def _reason(constraints: dict[str, Any], value: Any, field_type: FieldType) -> ReasonCode:
    checks = _check(constraints, value, field_type)
    return next(check.reason for check in checks if check.outcome is not Outcome.PASSED)


class TestEnumAndConst:
    def test_a_member_passes(self) -> None:
        assert _passes({"enum": ["EUR", "USD"]}, "EUR", FieldType.STRING)

    def test_a_non_member_fails(self) -> None:
        assert _reason({"enum": ["EUR", "USD"]}, "GBP", FieldType.STRING) is ReasonCode.NOT_IN_ENUM

    def test_case_is_significant(self) -> None:
        """`eur` is not `EUR`. Folding it would be a correction nobody asked for."""
        assert not _passes({"enum": ["EUR"]}, "eur", FieldType.STRING)

    def test_surrounding_whitespace_is_significant(self) -> None:
        assert not _passes({"enum": ["EUR"]}, " EUR", FieldType.STRING)

    def test_const_holds_the_one_value(self) -> None:
        assert _passes({"const": "INVOICE"}, "INVOICE", FieldType.STRING)
        assert (
            _reason({"const": "INVOICE"}, "RECEIPT", FieldType.STRING)
            is ReasonCode.NOT_THE_CONSTANT
        )

    def test_a_decimal_enum_declared_as_text_compares_by_value(self) -> None:
        """The schema is JSON, so `1.00` is written as text; the value is a Decimal."""
        assert _passes({"enum": ["1.00", "2.00"]}, Decimal("1.00"), FieldType.DECIMAL)
        assert _passes({"enum": ["1.00"]}, Decimal("1.0"), FieldType.DECIMAL)


class TestPattern:
    def test_a_matching_value_passes(self) -> None:
        assert _passes({"pattern": r"INV-\d{4}"}, "INV-2026", FieldType.STRING)

    @pytest.mark.parametrize(
        ("value", "why"),
        [
            ("INV-2026yy", "trailing junk"),
            ("xxINV-2026", "leading junk"),
            ("xxINV-2026yy", "junk on both sides"),
        ],
    )
    def test_the_whole_value_must_match(self, value: str, why: str) -> None:
        """FR-024 — a substring match would accept anything containing four digits.

        The three cases are separate because the both-sides case alone does not
        pin the property. A mutation run found that a matcher tolerating a
        **leading** prefix survived the original test, which used
        `"xxINV-2026yy"`: junk at the end failed it, so junk at the start was
        never exercised. Leading is also the likelier bug — a matcher written with
        `search` instead of `fullmatch` anchors at the end more often than at the
        start.
        """
        assert not _passes({"pattern": r"INV-\d{4}"}, value, FieldType.STRING), why

    def test_a_newline_does_not_end_the_value(self) -> None:
        assert not _passes({"pattern": r"INV-\d{4}"}, "INV-2026\nrubbish", FieldType.STRING)

    def test_a_failure_says_which_constraint_broke(self) -> None:
        assert _reason({"pattern": r"\d+"}, "abc", FieldType.STRING) is ReasonCode.PATTERN_UNMATCHED


class TestBounds:
    @pytest.mark.parametrize(
        ("constraints", "value", "expected"),
        [
            ({"minimum": 0}, Decimal("0"), True),
            ({"minimum": 0}, Decimal("-0.01"), False),
            ({"maximum": "1000.00"}, Decimal("1000.00"), True),
            ({"maximum": "1000.00"}, Decimal("1000.01"), False),
            ({"multiple_of": "0.01"}, Decimal("12.34"), True),
            ({"multiple_of": "0.01"}, Decimal("12.345"), False),
        ],
    )
    def test_numeric_bounds(self, constraints: dict, value: Decimal, expected: bool) -> None:
        assert _passes(constraints, value, FieldType.DECIMAL) is expected

    def test_a_bound_on_a_date_compares_dates(self) -> None:
        assert _passes({"minimum": "2026-01-01"}, date(2026, 5, 1), FieldType.DATE)
        assert not _passes({"minimum": "2026-01-01"}, date(2025, 12, 31), FieldType.DATE)

    def test_the_reason_says_which_side_was_exceeded(self) -> None:
        low = _reason({"minimum": 5}, Decimal("1"), FieldType.DECIMAL)
        high = _reason({"maximum": 5}, Decimal("9"), FieldType.DECIMAL)
        assert low is ReasonCode.BELOW_MINIMUM
        assert high is ReasonCode.ABOVE_MAXIMUM

    def test_a_multiple_is_computed_in_exact_decimal(self) -> None:
        """0.1 + 0.2 arithmetic must not decide whether an amount is a valid price."""
        assert _passes({"multiple_of": "0.1"}, Decimal("0.3"), FieldType.DECIMAL)


class TestLengths:
    def test_length_counts_unicode_code_points(self) -> None:
        """FR-023 — not bytes, which would make a bound mean less in some scripts."""
        vietnamese = "Cảm ơn"  # 6 code points, 9 UTF-8 bytes
        assert _passes({"max_length": 6}, vietnamese, FieldType.STRING)
        assert not _passes({"max_length": 5}, vietnamese, FieldType.STRING)

    def test_an_astral_character_counts_once(self) -> None:
        assert _passes({"max_length": 1}, "\U0001f600", FieldType.STRING)

    def test_bounds_are_inclusive(self) -> None:
        assert _passes({"min_length": 3}, "abc", FieldType.STRING)
        assert not _passes({"min_length": 4}, "abc", FieldType.STRING)


def test_a_repeating_group_length_bound_counts_entries() -> None:
    from docdoc.extraction.schema import Schema

    base = artifacts.build().schema
    lines = next(field for field in base.fields if field.name == "line_items")
    schema = Schema(
        name=base.name,
        version=base.version,
        fields=tuple(
            field.model_copy(update={"constraints": {"min_length": 3}}) if field is lines else field
            for field in base.fields
        ),
    )
    pair = artifacts.build(schema=schema)
    result = validate(pair.extraction, pair.grounding, schema)
    finding = next(f for f in result.findings if f.field_path == "line_items")
    assert finding.reason is ReasonCode.TOO_SHORT
    assert finding.actual == "2 entries"


def test_no_constraint_check_is_declared_for_an_absent_value() -> None:
    """An obligation exists where it applies.

    `notes` is absent, so its `max_length` has nothing to bound. Declaring a check
    anyway and marking it not-evaluated would make `incomplete` the
    verdict of nearly every real document, and the state would stop carrying
    information.
    """
    pair = artifacts.build()
    result = validate(pair.extraction, pair.grounding, pair.schema)
    assert result.check("notes#max_length") is None
    assert result.verdict is Verdict.VALID


def test_a_constraint_check_that_passes_is_recorded() -> None:
    pair = artifacts.build()
    result = validate(pair.extraction, pair.grounding, pair.schema)
    check = result.check("number#pattern")
    assert check is not None
    assert check.outcome is Outcome.PASSED
