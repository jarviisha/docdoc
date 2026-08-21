"""T025 — `comparators@1` compares typed values and coerces nothing (FR-023, FR-024, SC-002).

The type-identity gate is the requirement rather than defensive coding, and the
reason is that Python's ``==`` is *too* generous in exactly the places an invoice
lives:

    True == 1              -> True
    Decimal(1) == True     -> True
    1 == 1.0               -> True

Without ``type(a) is type(b)``, a boolean label silently matches an integer
prediction and the report calls it correct. That is the cross-type coercion
FR-024 forbids, arriving through the back door -- and it is invisible, because
the outcome it produces is ``CORRECT``.

``isinstance`` does not fix it and is actively wrong here: ``bool`` subclasses
``int``, so an isinstance gate accepts ``True`` as an ``int``.

What the gate deliberately does **not** do is reject representational difference
*within* a type. ``Decimal("1240.00") == Decimal("1240.0")`` is ``True`` and must
stay true: trailing zeros are representation, not value, and comparing typed
values is what absorbs that whole class of difference without anyone writing a
normalization rule.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

import pytest

from docdoc.evaluation.comparators import (
    COMPARATOR_VERSIONS,
    EXACT,
    comparator_version_for,
    equal,
)

# -- what must compare equal -------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "predicted"),
    [
        (Decimal("1240.00"), Decimal("1240.0")),
        (Decimal("1240.00"), Decimal("1240")),
        (Decimal("0.10"), Decimal("0.1")),
        ("INV-001", "INV-001"),
        (date(2026, 3, 1), date(2026, 3, 1)),
        (datetime(2026, 3, 2, 14, 5), datetime(2026, 3, 2, 14, 5)),
        (2, 2),
        (True, True),
        (1.5, 1.5),
    ],
)
def test_equal_values_compare_equal(expected: object, predicted: object) -> None:
    assert equal(expected, predicted)


def test_trailing_zeros_are_representation_not_value() -> None:
    """Stated on its own because it is the case a reader will doubt.

    A decimal comparator that rejected ``1240.00`` against ``1240.0`` would mark
    a perfectly extracted total wrong on the basis of how many zeros the document
    happened to print -- and the "fix" would be a normalization rule, which
    FR-024 forbids for good reason. Comparing decimals *as decimals* means no
    rule is needed.
    """
    assert equal(Decimal("1240.00"), Decimal("1240.0"))
    assert str(Decimal("1240.00")) != str(Decimal("1240.0")), (
        "if these ever render identically the case has stopped being interesting"
    )


# -- the type gate -----------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "predicted", "why"),
    [
        (True, 1, "bool subclasses int, so isinstance would accept this"),
        (1, True, "and the same in the other direction"),
        (False, 0, "the falsy pair, which reads as harmless and is not"),
        (1, 1.0, "an integer answer and a float answer are different answers"),
        (1.0, 1, "likewise"),
        (Decimal("1"), 1, "a decimal total must never match a bare int"),
        (Decimal("1"), True, "Decimal(1) == True is True in Python"),
        (Decimal("1240.00"), "1240.00", "the string form is not the value"),
        ("2026-03-01", date(2026, 3, 1), "nor is the ISO rendering of a date"),
        (date(2026, 3, 1), datetime(2026, 3, 1), "a date is not a datetime at midnight"),
    ],
)
def test_cross_type_pairs_never_match(expected: object, predicted: object, why: str) -> None:
    assert not equal(expected, predicted), why


def test_python_would_have_said_yes() -> None:
    """The guard on the guard: these pairs are equal *to Python*.

    If this ever fails, Python's semantics changed and the gate above is
    protecting against nothing -- which is worth knowing, because the tests above
    would still pass and would still look meaningful.
    """
    assert True == 1
    assert Decimal(1) == True  # noqa: E712
    assert 1 == 1.0


# -- no normalization --------------------------------------------------------


@pytest.mark.parametrize(
    ("expected", "predicted"),
    [
        ("ACME LTD", "acme ltd"),
        ("ACME LTD", "ACME  LTD"),
        ("ACME LTD", " ACME LTD"),
        ("ACME LTD", "ACME LTD "),
        ("ACME LTD", "ACME LTD."),
        ("café", "cafe"),
    ],
)
def test_strings_are_not_normalized_case_folded_or_trimmed(expected: str, predicted: str) -> None:
    """FR-024. Any leniency is a new comparator with a new version, never a quiet ``.strip()``.

    Each of these is individually defensible and collectively fatal: once one
    normalization is acceptable the next one is too, and the report stops being
    able to say what it compared.
    """
    assert not equal(expected, predicted)


@pytest.mark.parametrize(
    ("expected", "predicted"),
    [
        (Decimal("1240.004"), Decimal("1240.00")),
        (Decimal("1240.005"), Decimal("1240.01")),
        (1.0000001, 1.0),
    ],
)
def test_numbers_are_not_rounded(expected: object, predicted: object) -> None:
    assert not equal(expected, predicted)


# -- the registry ------------------------------------------------------------


def test_every_field_type_has_a_comparator_version() -> None:
    """Keyed by ``FieldType`` so a future leniency is data, not an ``if`` in the scorer."""
    from docdoc.extraction.schema import FieldType

    missing = [kind for kind in FieldType if str(kind) not in COMPARATOR_VERSIONS]
    assert not missing, (
        f"{missing} have no comparator version, so an outcome on one of those fields "
        "would record a version the report cannot explain"
    )


def test_the_mvp_uses_exactly_one_comparator() -> None:
    """A set of one, versioned anyway -- which is where the discipline is cheapest."""
    assert set(COMPARATOR_VERSIONS.values()) == {EXACT}


def test_an_unknown_field_type_falls_back_to_exact() -> None:
    """Never to leniency. An unrecognised type must not become a looser comparison."""
    assert comparator_version_for(None) == EXACT
    assert comparator_version_for("not-a-type") == EXACT


def test_none_never_matches_a_value() -> None:
    """``None`` is absence, and absence is decided by the label's expectation.

    The comparator is never asked to decide whether "nothing" equals "something";
    ``_classify`` resolves that against ``Expectation`` first. Pinned anyway,
    because a comparator that answered ``True`` here would make every missing
    field correct.
    """
    assert not equal(None, "INV-001")
    assert not equal("INV-001", None)
    assert equal(None, None), "two absences are the same absence"
