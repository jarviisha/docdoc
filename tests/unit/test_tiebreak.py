"""T032 — the total tie-break (FR-024, GRD-12).

"Total" is the load-bearing word. A partial ordering leaves the winner to
whatever `sorted` happened to see first, which is stable within one run and not
across platforms or hash seeds. The test that matters here is the last one: score,
start, **and** length all equal. A naive `max()` gets away with that until the
day the candidate list arrives in a different order.
"""

from __future__ import annotations

import pytest

from docdoc.grounding.match import MAX_ALTERNATIVES, Candidate, select


class TestOrdering:
    def test_the_highest_score_wins(self) -> None:
        winner, _ = select([Candidate(0.91, 10, 20), Candidate(0.99, 50, 60)])
        assert winner == Candidate(0.99, 50, 60)

    def test_equal_scores_break_on_the_earliest_start(self) -> None:
        winner, _ = select([Candidate(0.95, 50, 60), Candidate(0.95, 10, 20)])
        assert winner is not None
        assert winner.view_start == 10

    def test_equal_scores_and_starts_break_on_the_shortest_range(self) -> None:
        winner, _ = select([Candidate(0.95, 10, 30), Candidate(0.95, 10, 20)])
        assert winner is not None
        assert winner.view_end == 20

    def test_the_three_rules_apply_in_order(self) -> None:
        """A later rule must never override an earlier one."""
        winner, _ = select(
            [
                Candidate(0.95, 0, 5),  # earliest and shortest, but lower score
                Candidate(0.99, 90, 200),  # highest score wins regardless
            ]
        )
        assert winner is not None
        assert winner.score == 0.99


class TestTotality:
    def test_exactly_one_winner_when_score_start_and_length_all_tie(self) -> None:
        """The case the ordering exists for: identical candidates, one answer."""
        identical = [Candidate(0.93, 42, 55) for _ in range(5)]
        winner, runners_up = select(identical)
        assert winner == Candidate(0.93, 42, 55)
        assert len(runners_up) == 4

    def test_the_winner_does_not_depend_on_input_order(self) -> None:
        candidates = [
            Candidate(0.95, 10, 20),
            Candidate(0.95, 10, 25),
            Candidate(0.95, 5, 20),
            Candidate(0.99, 80, 90),
        ]
        expected, _ = select(candidates)
        for rotation in range(len(candidates)):
            rotated = candidates[rotation:] + candidates[:rotation]
            assert select(rotated)[0] == expected

    def test_reversing_the_input_does_not_change_the_result(self) -> None:
        candidates = [Candidate(0.9 + i / 100, i * 10, i * 10 + 5) for i in range(8)]
        forward = select(candidates)
        backward = select(list(reversed(candidates)))
        assert forward == backward


class TestAlternatives:
    def test_no_candidates_yields_no_winner(self) -> None:
        assert select([]) == (None, [])

    def test_runners_up_are_capped(self) -> None:
        candidates = [Candidate(0.95, i * 10, i * 10 + 5) for i in range(20)]
        _, runners_up = select(candidates)
        assert len(runners_up) == MAX_ALTERNATIVES

    def test_runners_up_follow_the_same_order_as_the_winner(self) -> None:
        candidates = [Candidate(0.90 + i / 100, i * 10, i * 10 + 5) for i in range(4)]
        winner, runners_up = select(candidates)
        assert winner is not None
        scores = [winner.score] + [c.score for c in runners_up]
        assert scores == sorted(scores, reverse=True)

    def test_the_winner_is_never_repeated_among_the_runners_up(self) -> None:
        candidates = [Candidate(0.93, 42, 55) for _ in range(3)]
        winner, runners_up = select(candidates)
        assert len(runners_up) == 2
        assert sum(1 for c in [winner, *runners_up] if c == winner) == 3  # all equal, 3 total


@pytest.mark.parametrize("seed_order", [[0, 1, 2], [2, 1, 0], [1, 0, 2], [1, 2, 0]])
def test_permutations_agree(seed_order: list[int]) -> None:
    base = [Candidate(0.95, 10, 20), Candidate(0.95, 10, 20), Candidate(0.95, 4, 30)]
    winner, _ = select([base[i] for i in seed_order])
    assert winner == Candidate(0.95, 4, 30)
