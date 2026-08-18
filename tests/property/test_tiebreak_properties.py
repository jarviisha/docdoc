"""T045 — the tie-break is total, over generated candidate sets (FR-024, FR-028).

**Run this under two `PYTHONHASHSEED` values.** `PYTHONHASHSEED` randomises string
hashing per process, so a set iterated directly yields a different order each run.
The winner is immune by construction -- the ordering is total -- but the
*alternatives list* is not, and a single-seeded suite passes a non-deterministic
alternatives list by luck. CI runs both seeds; see `.github/workflows/ci.yml`.
"""

from __future__ import annotations

from hypothesis import given
from hypothesis import strategies as st

from docdoc.grounding.match import MAX_ALTERNATIVES, Candidate, _order, select

# Small ranges so ties in score, start, and length all occur often. A generator
# that never produces a tie tests nothing about a tie-break.
SCORES = st.sampled_from([0.90, 0.91, 0.95, 0.99, 1.0])
STARTS = st.integers(min_value=0, max_value=6)
LENGTHS = st.integers(min_value=1, max_value=4)

CANDIDATE = st.builds(
    lambda s, start, length: Candidate(s, start, start + length), SCORES, STARTS, LENGTHS
)
CANDIDATES = st.lists(CANDIDATE, min_size=1, max_size=25)


@given(CANDIDATES)
def test_exactly_one_winner_exists(candidates: list[Candidate]) -> None:
    winner, _ = select(candidates)
    assert winner is not None
    assert winner in candidates


@given(CANDIDATES)
def test_the_winner_is_minimal_under_the_ordering(candidates: list[Candidate]) -> None:
    """Nothing in the set may order before the winner."""
    winner, _ = select(candidates)
    assert winner is not None
    assert all(_order(winner) <= _order(c) for c in candidates)


@given(CANDIDATES, st.randoms(use_true_random=False))
def test_the_result_is_independent_of_input_order(candidates: list[Candidate], rng) -> None:
    """The property a partial ordering would fail, and the reason `_order` is total."""
    expected = select(candidates)
    shuffled = list(candidates)
    rng.shuffle(shuffled)
    assert select(shuffled) == expected


@given(CANDIDATES)
def test_alternatives_are_ordered_and_capped(candidates: list[Candidate]) -> None:
    winner, runners_up = select(candidates)
    assert winner is not None
    assert len(runners_up) <= MAX_ALTERNATIVES
    keys = [_order(winner), *(_order(c) for c in runners_up)]
    assert keys == sorted(keys)


@given(CANDIDATES)
def test_selecting_twice_gives_the_same_answer(candidates: list[Candidate]) -> None:
    assert select(candidates) == select(candidates)


@given(CANDIDATES)
def test_no_candidate_is_lost_or_invented(candidates: list[Candidate]) -> None:
    winner, runners_up = select(candidates)
    assert winner is not None
    for candidate in [winner, *runners_up]:
        assert candidate in candidates
    assert len([winner, *runners_up]) == min(len(candidates), MAX_ALTERNATIVES + 1)
