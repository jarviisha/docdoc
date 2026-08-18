"""T014 — the de-hyphenation rule, and the three cases that decided it (GRD-3).

Both obvious rules are measurably wrong, and the numbers below are why the
implementation has a case-based one instead. **This file is also the tripwire for
any future threshold change**: the last test asserts a similarity of exactly
0.900, which is what a compound word broken at a line end scores. Raise the
threshold above 0.90 and it fails -- deliberately.
"""

from __future__ import annotations

import pytest
from rapidfuzz.distance import Levenshtein

from docdoc.grounding.options import DEFAULT_THRESHOLD
from docdoc.grounding.view import _fold as fold
from tests.fixtures.grounding import COMPOUND_BROKEN, IDENTIFIER_BROKEN, LOWERCASE_BROKEN


class TestTheRule:
    def test_a_lowercase_word_broken_at_a_line_end_is_joined(self) -> None:
        assert fold(LOWERCASE_BROKEN)[0] == "Total amount payable 1,240.00"

    def test_an_identifier_keeps_its_hyphen(self) -> None:
        assert fold(IDENTIFIER_BROKEN)[0] == "Reference INV-2024-001 approved"

    def test_a_hyphen_not_at_a_line_break_is_never_touched(self) -> None:
        assert fold("a well-known supplier")[0] == "a well-known supplier"

    def test_a_digit_after_the_break_keeps_the_hyphen(self) -> None:
        assert fold("total-\n240 due")[0] == "total-240 due"

    def test_an_uppercase_letter_after_the_break_keeps_the_hyphen(self) -> None:
        assert fold("acme-\nSUPPLIES")[0] == "acme-SUPPLIES"

    def test_the_unicode_hyphen_is_handled_like_the_ascii_one(self) -> None:
        assert fold("am‐\nount")[0] == "amount"


class TestWhyTheObviousRulesWereRejected:
    """The measurements from research.md R7, asserted rather than quoted.

    Neither failure is rescued by the fuzzy tier: both land below the threshold,
    so a wrong rule here loses the value outright.
    """

    def test_never_de_hyphenating_would_lose_an_ordinary_word(self) -> None:
        score = Levenshtein.normalized_similarity("amount", "am-ount")
        assert score == pytest.approx(0.857, abs=0.001)
        assert score < DEFAULT_THRESHOLD

    def test_always_de_hyphenating_would_lose_an_identifier(self) -> None:
        score = Levenshtein.normalized_similarity("INV-2024-001", "INV2024001")
        assert score == pytest.approx(0.833, abs=0.001)
        assert score < DEFAULT_THRESHOLD


class TestTheResidualLoss:
    """The case the chosen rule gets wrong, clearing the threshold by nothing.

    A genuine compound word broken at a line end is joined -- both sides are
    lowercase, so the rule cannot tell it from a justified break. It then scores
    EXACTLY 0.900 against what the model quoted.
    """

    def test_a_compound_word_is_joined_and_therefore_misses_the_exact_tier(self) -> None:
        assert fold(COMPOUND_BROKEN)[0] == "A wellknown supplier of parts"

    def test_it_clears_the_threshold_by_exactly_nothing(self) -> None:
        score = Levenshtein.normalized_similarity("well-known", "wellknown")
        assert score == pytest.approx(0.900, abs=1e-9)
        assert score >= DEFAULT_THRESHOLD, (
            "This is the tripwire. If the default threshold has been raised above "
            "0.90, compound words broken at a line end stop grounding. That is a "
            "real regression in recall, and the Milestone 6 tuning must measure it "
            "deliberately rather than discover it here (research.md R7)."
        )
