"""T022 — arithmetic, against an oracle that is not the implementation.

`fractions.Fraction` is exact and unrelated to `Decimal`, which is what makes it
a real oracle: if both agreed only because they share an implementation, the test
would be checking that a function equals itself.

The `number` case is the honest one. Milestone 3 parses a `number` field to a
Python `float`, so precision is already spent before this stage sees the value.
What is asserted here is the part validation controls: the float is read through
`Decimal(str(v))` and never `Decimal(v)`, and the reading is identical on every
platform.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction

from hypothesis import given, settings
from hypothesis import strategies as st

from docdoc.validation.numeric import as_decimal, render, within_tolerance

_AMOUNTS = st.decimals(
    min_value=Decimal("-1000000"),
    max_value=Decimal("1000000"),
    allow_nan=False,
    allow_infinity=False,
    places=4,
)
_TOLERANCES = st.decimals(
    min_value=Decimal(0), max_value=Decimal("10"), allow_nan=False, allow_infinity=False, places=4
)


@given(_AMOUNTS, _AMOUNTS, _TOLERANCES)
@settings(max_examples=400)
def test_tolerance_agrees_with_exact_rational_arithmetic(
    left: Decimal, right: Decimal, tolerance: Decimal
) -> None:
    expected = abs(Fraction(left) - Fraction(right)) <= Fraction(tolerance)
    assert within_tolerance(left, right, tolerance) is expected


@given(st.lists(_AMOUNTS, max_size=30), _AMOUNTS)
@settings(max_examples=300)
def test_a_sum_agrees_with_exact_rational_arithmetic(
    amounts: list[Decimal], total: Decimal
) -> None:
    """The sum rule's core, checked against rationals rather than against itself."""
    running = Decimal(0)
    for amount in amounts:
        running += amount
    exact = sum((Fraction(amount) for amount in amounts), Fraction(0))
    assert Fraction(running) == exact
    assert within_tolerance(running, total, Decimal(0)) is (exact == Fraction(total))


@given(_AMOUNTS)
@settings(max_examples=200)
def test_scale_never_changes_a_comparison(amount: Decimal) -> None:
    """`1240.0` and `1240.00` are the same amount and must compare equal."""
    padded = amount.quantize(Decimal("0.000001"))
    assert within_tolerance(amount, padded, Decimal(0))


@given(st.floats(allow_nan=False, allow_infinity=False, width=32))
@settings(max_examples=300)
def test_a_float_is_read_through_its_shortest_representation(value: float) -> None:
    """`Decimal(str(v))`, never `Decimal(v)` — the difference is 1240.10 against 1240.0999…"""
    read = as_decimal(value)
    assert read == Decimal(str(value))
    assert float(read) == value


def test_the_measured_case_that_decided_the_rule() -> None:
    """The measurement from research.md R3, restated as an assertion.

    The float is bound to a name rather than written inline because `ruff`'s
    RUF032 rewrites a literal `Decimal(1240.10)` into `Decimal("1240.10")` — which
    is exactly the bug this test exists to catch, applied by a linter. Keeping the
    float in a variable preserves what is being compared.
    """
    stated = 1240.10
    assert as_decimal(stated) == Decimal("1240.10")
    assert Decimal(stated) != Decimal("1240.10")
    assert str(Decimal(stated)).startswith("1240.0999")


def test_a_bool_is_not_a_number() -> None:
    """`True` is an `int` in Python, and letting it sum as 1 would be silent nonsense."""
    assert as_decimal(True) is None
    assert as_decimal(False) is None


@given(_AMOUNTS)
@settings(max_examples=200)
def test_rendering_is_stable_and_never_scientific(amount: Decimal) -> None:
    """Two runs must produce the same `expected` and `actual` text for one fact."""
    text = render(amount)
    assert text == render(amount)
    assert "E" not in text
    assert "e" not in text
    assert Decimal(text) == amount
