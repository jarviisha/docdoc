"""T030, T033 — the candidate filter and its budget (GRD-10, GRD-11, research.md R3, R4).

Two properties carry this feature's recall claim:

* ``k`` is *derived*, so the slack is not a number anyone can tune loose.
* the filter is **complete**, so "ungrounded" means the text is not there rather
  than that the search gave up.

If the second fails, every other guarantee in the layer is describing a search
that quietly misses things.
"""

from __future__ import annotations

import pytest
from rapidfuzz.distance import Levenshtein

from docdoc.grounding.candidates import blocks, candidate_starts, max_edits, window_lengths
from docdoc.grounding.options import DEFAULT_THRESHOLD
from tests.fixtures.grounding import ADVERSARIAL_CLAIMS, ADVERSARIAL_TEXT

T = DEFAULT_THRESHOLD


class TestTheEditBudgetIsDerived:
    def test_the_closed_form_matches_the_self_referential_definition(self) -> None:
        """``k = floor((1-t)*m/t)`` must agree with ``k = floor((1-t)*(m+k))``.

        The second is the definition -- a window may be up to ``m + k`` long, and
        the similarity divides by the longer of the two. The first is what the
        code computes. They agree exactly, which is why the slack needs no
        independent constant.
        """
        for m in range(1, 200):
            k = max_edits(m, T)
            assert k == int((1.0 - T) * (m + k)), m

    @pytest.mark.parametrize(
        ("length", "expected"), [(5, 0), (10, 1), (16, 1), (24, 2), (30, 3), (50, 5)]
    )
    def test_the_documented_values(self, length: int, expected: int) -> None:
        assert max_edits(length, T) == expected

    def test_short_claims_degenerate_to_exact_search(self) -> None:
        """``k = 0`` up to nine characters, and that is the threshold agreeing with itself.

        A single edit in a nine-character string scores 0.889 and does not clear
        0.90, so there is nothing for an edit budget to allow.
        """
        assert all(max_edits(m, T) == 0 for m in range(1, 10))
        assert Levenshtein.normalized_similarity("123456789", "12345678X") < T

    def test_window_lengths_bracket_the_claim_by_exactly_k(self) -> None:
        lengths = window_lengths(30, T)
        assert lengths.start == 30 - 3
        assert lengths.stop - 1 == 30 + 3


class TestBlocks:
    def test_there_are_k_plus_one_disjoint_blocks_covering_the_claim(self) -> None:
        claim = "abcdefghijklmnopqrstuvwxyz1234"
        k = max_edits(len(claim), T)
        parts = blocks(claim, k)
        assert len(parts) == k + 1
        assert "".join(text for _, text in parts) == claim
        for offset, text in parts:
            assert claim[offset : offset + len(text)] == text


class TestCompleteness:
    """GRD-11 — no window at or above the threshold can be missed.

    Checked against brute force: score *every* window in the text and confirm the
    filter's candidate set contains every start that clears the threshold.
    """

    @pytest.mark.parametrize(
        "claim",
        ["Total 1,240.00", "INV-2024-001", "ACME SUPPLIES", "the quick brown fox jumps"],
    )
    def test_the_filter_finds_every_window_brute_force_finds(self, claim: str) -> None:
        text = (
            "Header ACME SUPPLIES LIMITED\nTotal 1,24O.00 due\nRef INV-2024-O01\n"
            "the quik brown fox jumps over\nTotal 1,240.OO\n"
        )
        found = candidate_starts(claim=claim, view_text=text, threshold=T, budget=10_000)

        brute: set[int] = set()
        for start in range(len(text)):
            for length in window_lengths(len(claim), T):
                end = start + length
                if end > len(text):
                    break
                if Levenshtein.normalized_similarity(claim, text[start:end], score_cutoff=T):
                    brute.add(start)

        assert brute <= set(found.starts), f"filter missed starts {brute - set(found.starts)}"

    def test_completeness_survives_a_threshold_too_low_to_split_the_claim(self) -> None:
        """The regression this file exists to prevent a second time.

        At low thresholds ``k`` approaches the claim length, so ``k + 1``
        non-empty blocks do not exist and the pigeonhole argument stops applying.
        An earlier implementation degraded to a single block containing the whole
        claim, which silently required a *verbatim* match -- the exact opposite of
        what lowering the threshold asks for. The filter now falls back to an
        exhaustive scan instead, so completeness holds at every threshold.
        """
        text = "Total 1,240.00 due on 2026-04-13"
        claim = "Total 9,999.99 due on 1999-01-01"
        low = 0.5
        assert max_edits(len(claim), low) + 1 > len(claim), "premise: cannot be split"

        found = candidate_starts(claim=claim, view_text=text, threshold=low, budget=10_000)
        brute = {
            start
            for start in range(len(text))
            for length in window_lengths(len(claim), low)
            if start + length <= len(text)
            and Levenshtein.normalized_similarity(
                claim, text[start : start + length], score_cutoff=low
            )
        }
        assert brute
        assert brute <= set(found.starts)

    def test_a_claim_present_verbatim_is_always_among_the_candidates(self) -> None:
        text = "prefix Total 1,240.00 suffix"
        claim = "Total 1,240.00"
        found = candidate_starts(claim=claim, view_text=text, threshold=T, budget=10_000)
        assert text.index(claim) in found.starts


class TestOrdering:
    def test_starts_are_sorted_before_anything_sees_them(self) -> None:
        """Sorting before scoring is what keeps the alternatives list stable (R14)."""
        found = candidate_starts(
            claim="Total Amount", view_text=ADVERSARIAL_TEXT[:5_000], threshold=T, budget=10_000
        )
        assert list(found.starts) == sorted(found.starts)


class TestTheBudget:
    """research.md R8 — the cap is real, and it is never silent."""

    def test_an_ordinary_document_never_reaches_it(self) -> None:
        found = candidate_starts(
            claim="Total 1,240.00",
            view_text="Invoice\nTotal 1,240.00\nThank you",
            threshold=T,
            budget=1_500,
        )
        assert not found.truncated
        assert len(found.starts) < 50

    def test_the_adversarial_fixture_does_reach_it(self) -> None:
        found = candidate_starts(
            claim=ADVERSARIAL_CLAIMS[2], view_text=ADVERSARIAL_TEXT, threshold=T, budget=1_500
        )
        assert found.truncated
        assert len(found.starts) == 1_500

    def test_truncation_keeps_a_deterministic_prefix(self) -> None:
        """A sorted prefix, not a sample: which candidates survive must not vary."""
        kwargs = {
            "claim": ADVERSARIAL_CLAIMS[1],
            "view_text": ADVERSARIAL_TEXT,
            "threshold": T,
            "budget": 1_500,
        }
        first = candidate_starts(**kwargs)
        second = candidate_starts(**kwargs)
        assert first.starts == second.starts
        assert list(first.starts) == sorted(first.starts)
