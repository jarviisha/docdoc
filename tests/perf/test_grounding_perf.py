"""T055 — SC-020's budgets, and proof the candidate budget is doing the work.

Targets sit far above the measurements on purpose, for the reason Milestones 2
and 3 both recorded: a perf test that trips on machine noise gets disabled, and a
disabled test protects nothing. What these catch is a **shape** change -- a match
view rebuilt per value instead of per run, or a candidate filter that lost its
length bound and went quadratic -- not constant-factor drift.

The adversarial row is the one to read. It measures the same input twice, with
and without the budget, and asserts the budget is what keeps SC-020 true. A
budget that never fires would pass a naive "is it fast?" test while protecting
nothing at all.
"""

from __future__ import annotations

import random
import string
import time

import pytest

from docdoc.grounding import GroundingOptions, ground
from docdoc.grounding.view import MatchView, clear_view_cache
from tests.fixtures.grounding import ADVERSARIAL_CLAIMS, ADVERSARIAL_TEXT
from tests.support import make_document, make_extracted, make_extraction

pytestmark = pytest.mark.perf

BEST_OF = 5


def best_of(fn, n: int = BEST_OF) -> float:
    """Milliseconds, best of N. Best-of rather than a single sample, because a
    single sample measures whatever else the machine was doing."""
    best = float("inf")
    for _ in range(n):
        start = time.perf_counter()
        fn()
        best = min(best, (time.perf_counter() - start) * 1000)
    return best


@pytest.fixture(scope="module")
def big_document():
    """~50k characters of prose-like text -- roughly a 20-page document."""
    rng = random.Random(7)
    alphabet = string.ascii_lowercase + "  0123456789.,"
    return make_document("".join(rng.choice(alphabet) for _ in range(50_000)))


def extraction_of(document, claims: list[str]):
    return make_extraction(
        {f"f{i}": make_extracted(f"f{i}", value="x", claimed_text=c) for i, c in enumerate(claims)},
        document=document,
    )


def _cold_build(document):
    """Build a match view with the cache emptied first.

    Milestone 7 made ``MatchView.build`` cached, so timing it warm measures a
    dictionary lookup. Every budget in this file is about the cost of the *work*.
    """
    clear_view_cache()
    return MatchView.build(document)


def _cold_ground(document, extraction):
    """Ground with no cached view, so the fold is inside the measurement."""
    clear_view_cache()
    return ground(document, extraction)


class TestTheMatchViewIsBuiltOncePerRun:
    """FR-019 -- the row that would move if this regressed, and the reason for the file."""

    def test_building_it_once_is_within_budget(self, big_document) -> None:
        elapsed = best_of(lambda: _cold_build(big_document))
        assert elapsed < 200, f"match view construction took {elapsed:.1f} ms"

    def test_grounding_twenty_values_costs_about_one_view_plus_the_matching(
        self, big_document
    ) -> None:
        """If the view were rebuilt per value, this would be ~20x the row above.

        Asserted as a shape rather than as a number: twenty values must not cost
        twenty view builds, whatever the machine.

        **The baseline is a cold build, and has to be.** Milestone 7 gave
        ``MatchView.build`` a process-local cache (FR-020), so measuring it
        without clearing first measures a dictionary lookup — against which
        *any* real work looks like a per-value rebuild, and this test failed
        while the thing it guards had actually got faster.
        """
        claims = [big_document.text[i * 400 : i * 400 + 30] for i in range(20)]
        extraction = extraction_of(big_document, claims)

        one_view = best_of(lambda: _cold_build(big_document))
        twenty = best_of(lambda: _cold_ground(big_document, extraction))

        assert twenty < one_view * 10, (
            f"twenty values took {twenty:.1f} ms against {one_view:.1f} ms for a single "
            "view build -- the view looks like it is being rebuilt per value (FR-019)"
        )

    def test_a_second_grounding_of_one_document_reuses_the_view(self, big_document) -> None:
        """FR-020's actual payoff, which the shape test above cannot see.

        Several extractions grounding against **one** document inside one process
        is the case artifact reuse does not serve: each has its own extraction
        artifact, so each misses the store, and each would fold the same document
        again. This is the measurement that says it does not.
        """
        claims = [big_document.text[i * 400 : i * 400 + 30] for i in range(20)]
        extraction = extraction_of(big_document, claims)

        cold = best_of(lambda: _cold_ground(big_document, extraction))
        warm = best_of(lambda: ground(big_document, extraction))

        assert warm < cold, (
            f"a second grounding of the same document took {warm:.1f} ms against "
            f"{cold:.1f} ms cold -- the match view is not being reused (FR-020)"
        )


class TestSc020:
    def test_exact_tier_twenty_values(self, big_document) -> None:
        claims = [big_document.text[i * 400 : i * 400 + 30] for i in range(20)]
        extraction = extraction_of(big_document, claims)
        elapsed = best_of(lambda: ground(big_document, extraction))
        assert elapsed < 100, f"exact tier over 20 values took {elapsed:.1f} ms"

    def test_fuzzy_tier_twenty_values_on_ordinary_text(self, big_document) -> None:
        """Every claim carries an edit, so none short-circuits at the exact tier."""
        claims = []
        for i in range(20):
            window = big_document.text[i * 400 : i * 400 + 30]
            claims.append(window[:15] + "X" + window[16:])
        extraction = extraction_of(big_document, claims)
        elapsed = best_of(lambda: ground(big_document, extraction))
        assert elapsed < 500, f"fuzzy tier over 20 values took {elapsed:.1f} ms"


class TestTheCandidateBudgetIsLoadBearing:
    """research.md R8 — the adversarial case, with the budget and without it."""

    @staticmethod
    @pytest.fixture(scope="class")
    def adversarial():
        document = make_document(ADVERSARIAL_TEXT)
        # Twenty values of the worst shape: near-miss claims whose blocks are
        # near-ubiquitous, so none short-circuits at the exact tier.
        claims = [ADVERSARIAL_CLAIMS[2]] * 20
        return document, extraction_of(document, claims)

    def test_it_stays_within_sc020_with_the_default_budget(self, adversarial) -> None:
        document, extraction = adversarial
        elapsed = best_of(lambda: ground(document, extraction), n=2)
        assert elapsed < 500, (
            f"the adversarial fixture took {elapsed:.1f} ms with the default budget. "
            "SC-020 claims its bound holds for *any* input, and the budget is what "
            "makes that true."
        )

    def test_it_would_blow_the_budget_without_one(self, adversarial) -> None:
        """The measurement that sized the default. Without this, the test above
        would pass on a fixture that never needed protecting."""
        document, extraction = adversarial
        unbounded = best_of(
            lambda: ground(document, extraction, options=GroundingOptions(candidate_budget=10**9)),
            n=1,
        )
        bounded = best_of(lambda: ground(document, extraction), n=2)
        assert unbounded > bounded * 2, (
            f"unbounded {unbounded:.1f} ms vs bounded {bounded:.1f} ms -- the budget is "
            "not actually constraining this input, so it is not protecting SC-020"
        )

    def test_truncation_is_reported_rather_than_silent(self, adversarial) -> None:
        document, extraction = adversarial
        result = ground(document, extraction)
        assert result.counts.truncated == 20
        assert all(o.truncated for o in result.outcomes.values())
