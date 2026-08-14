"""T008 — Span invariants SP-1..SP-3 and derived operations (FR-004)."""

from __future__ import annotations

import pytest

from docdoc.kernel import Span, SpanError


class TestConstruction:
    def test_create_accepts_a_valid_range(self) -> None:
        span = Span.create(3, 7)
        assert (span.start, span.end) == (3, 7)

    def test_create_accepts_a_zero_length_range(self) -> None:
        """SP-3 — an empty span is valid, not an error."""
        assert Span.create(4, 4).is_empty

    def test_create_rejects_a_reversed_range(self) -> None:
        """SP-1 — start must not exceed end."""
        with pytest.raises(SpanError) as excinfo:
            Span.create(9, 2)
        assert excinfo.value.span == Span(9, 2)

    def test_create_rejects_a_negative_start(self) -> None:
        with pytest.raises(SpanError):
            Span.create(-1, 5)

    def test_create_rejects_non_integers(self) -> None:
        with pytest.raises(SpanError):
            Span.create(1.5, 5)  # type: ignore[arg-type]

    def test_create_rejects_booleans(self) -> None:
        """bool is an int subclass; accepting it would let True silently mean 1."""
        with pytest.raises(SpanError):
            Span.create(True, 5)  # type: ignore[arg-type]


class TestLength:
    def test_len_is_character_length_not_field_count(self) -> None:
        """__len__ is deliberately overridden; a Span still has two fields."""
        span = Span(10, 25)
        assert len(span) == 15
        start, end = span
        assert (start, end) == (10, 25)
        assert span[0] == 10

    def test_an_empty_span_is_falsy(self) -> None:
        """Consequence of the __len__ override, asserted so it stays intentional."""
        assert not Span(4, 4)
        assert Span(4, 5)


class TestPredicates:
    def test_contains_uses_half_open_semantics(self) -> None:
        span = Span(3, 6)
        assert span.contains(3)
        assert span.contains(5)
        assert not span.contains(6)
        assert not span.contains(2)

    def test_an_empty_span_contains_nothing(self) -> None:
        assert not Span(3, 3).contains(3)

    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            (Span(0, 5), Span(3, 8), True),
            (Span(0, 5), Span(5, 8), False),  # adjacent, not overlapping
            (Span(0, 5), Span(6, 8), False),
            (Span(2, 4), Span(0, 10), True),
            (Span(3, 3), Span(0, 10), False),  # empty intersects nothing
        ],
    )
    def test_intersects(self, a: Span, b: Span, expected: bool) -> None:
        assert a.intersects(b) is expected
        assert b.intersects(a) is expected

    def test_contains_span(self) -> None:
        assert Span(0, 10).contains_span(Span(2, 5))
        assert Span(0, 10).contains_span(Span(0, 10))
        assert not Span(0, 10).contains_span(Span(5, 11))


class TestShift:
    def test_shift_moves_both_bounds(self) -> None:
        assert Span(3, 7).shift(10) == Span(13, 17)

    def test_shift_preserves_length(self) -> None:
        assert len(Span(3, 7).shift(-3)) == 4

    def test_shift_below_zero_is_rejected(self) -> None:
        """Rebasing must never silently produce a negative offset."""
        with pytest.raises(SpanError):
            Span(1, 4).shift(-5)

    def test_shift_round_trips(self) -> None:
        span = Span(12, 40)
        assert span.shift(100).shift(-100) == span


class TestValidationAgainstText:
    def test_within_bounds_passes(self) -> None:
        Span(0, 5).validate_within(5)

    def test_beyond_text_is_rejected(self) -> None:
        """SP-2 — spans are only meaningful against a specific text."""
        with pytest.raises(SpanError) as excinfo:
            Span(0, 6).validate_within(5)
        assert excinfo.value.text_length == 5

    def test_empty_span_at_end_of_text_is_valid(self) -> None:
        Span(5, 5).validate_within(5)
