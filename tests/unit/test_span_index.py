"""T011 — SpanIndex agrees with brute force and stays O(log n) correct."""

from __future__ import annotations

import pytest

from docdoc.kernel import Span, SpanIndex, Token


def token_at(start: int, end: int) -> Token:
    return Token(span=Span(start, end), geometry=None, source_confidence=None)


def brute_force_tokens_in(tokens: tuple[Token, ...], query: Span) -> tuple[Token, ...]:
    """The obvious O(n) implementation, used as the oracle."""
    return tuple(t for t in tokens if t.span.intersects(query))


SAMPLE = (
    token_at(0, 3),
    token_at(4, 7),
    token_at(8, 12),
    token_at(20, 25),
    token_at(25, 30),  # adjacent, not overlapping
)


class TestConstruction:
    def test_empty_index(self) -> None:
        index = SpanIndex(())
        assert len(index) == 0
        assert index.tokens_in(Span(0, 10)) == ()

    def test_length_and_iteration(self) -> None:
        index = SpanIndex(SAMPLE)
        assert len(index) == 5
        assert tuple(index) == SAMPLE

    def test_indexing_returns_tokens_in_order(self) -> None:
        index = SpanIndex(SAMPLE)
        assert index[0] == SAMPLE[0]
        assert index[-1] == SAMPLE[-1]


class TestTokensIn:
    @pytest.mark.parametrize(
        "query",
        [
            Span(0, 0),  # empty at start
            Span(5, 5),  # empty inside a token
            Span(0, 3),  # exactly the first token
            Span(0, 4),  # first token plus a gap
            Span(1, 2),  # strictly inside one token
            Span(2, 5),  # straddles two tokens
            Span(3, 4),  # entirely in the gap between tokens
            Span(0, 30),  # everything
            Span(24, 26),  # straddles the adjacent pair
            Span(30, 40),  # past the last token
            Span(12, 20),  # the large gap
        ],
    )
    def test_agrees_with_brute_force(self, query: Span) -> None:
        index = SpanIndex(SAMPLE)
        assert index.tokens_in(query) == brute_force_tokens_in(SAMPLE, query)

    def test_empty_query_matches_nothing(self) -> None:
        """An empty span intersects nothing, so locate() can return () for it."""
        assert SpanIndex(SAMPLE).tokens_in(Span(5, 5)) == ()

    def test_results_are_in_document_order(self) -> None:
        result = SpanIndex(SAMPLE).tokens_in(Span(0, 30))
        starts = [t.span.start for t in result]
        assert starts == sorted(starts)


class TestTokenAt:
    def test_finds_the_covering_token(self) -> None:
        index = SpanIndex(SAMPLE)
        assert index.token_at(5) == SAMPLE[1]

    def test_returns_none_in_a_gap(self) -> None:
        assert SpanIndex(SAMPLE).token_at(3) is None

    def test_returns_none_past_the_end(self) -> None:
        assert SpanIndex(SAMPLE).token_at(999) is None

    def test_boundary_is_half_open(self) -> None:
        index = SpanIndex(SAMPLE)
        assert index.token_at(0) == SAMPLE[0]
        assert index.token_at(2) == SAMPLE[0]
        assert index.token_at(3) is None


class TestImmutability:
    def test_index_does_not_expose_a_mutable_sequence(self) -> None:
        index = SpanIndex(list(SAMPLE))
        assert isinstance(index.tokens, tuple)

    def test_mutating_the_source_list_does_not_affect_the_index(self) -> None:
        source = list(SAMPLE)
        index = SpanIndex(source)
        source.clear()
        assert len(index) == 5


class TestValueSemantics:
    def test_equality_is_structural(self) -> None:
        assert SpanIndex(SAMPLE) == SpanIndex(SAMPLE)
        assert SpanIndex(SAMPLE) != SpanIndex(SAMPLE[:2])

    def test_comparison_with_another_type_is_not_an_error(self) -> None:
        assert SpanIndex(SAMPLE) != "not an index"

    def test_hashable_so_a_frozen_document_can_hold_it(self) -> None:
        assert hash(SpanIndex(SAMPLE)) == hash(SpanIndex(SAMPLE))

    def test_repr_summarises_rather_than_dumping_every_token(self) -> None:
        assert repr(SpanIndex(SAMPLE)) == "SpanIndex(5 tokens)"
