"""T031 — the approximate tier, and the honesty of what it refuses (FR-023, FR-045).

Two things this file insists on. A near-miss resolves and says it was a near
miss. Everything else is `ungrounded` -- with no range, no score, and **no
exception**, because being able to say "I could not find it" is the outcome this
whole stage exists to produce.
"""

from __future__ import annotations

import pytest

from docdoc.grounding import GroundingOptions, GroundingStatus, ground
from docdoc.grounding.options import DEFAULT_THRESHOLD
from tests.support import make_document, make_extracted, make_extraction

TEXT = "ACME SUPPLIES LIMITED\nInvoice INV-2024-001\nTotal 1,240.00 due 2026-04-13"


def ground_one(claim: str | None, *, text: str = TEXT, **opts: object):
    doc = make_document(text)
    extraction = make_extraction(
        {"f": make_extracted("f", value="x", claimed_text=claim)}, document=doc
    )
    options = GroundingOptions(**opts) if opts else None
    return doc, ground(doc, extraction, options=options).outcomes["f"]


class TestANearMissResolves:
    def test_a_single_transposed_character_resolves_fuzzily(self) -> None:
        _, outcome = ground_one("Total 1,24O.00 due 2026-04-13")
        assert outcome.status is GroundingStatus.FUZZY
        assert outcome.span is not None

    def test_it_carries_the_measured_similarity(self) -> None:
        _, outcome = ground_one("Total 1,24O.00 due 2026-04-13")
        assert outcome.score is not None
        assert DEFAULT_THRESHOLD <= outcome.score < 1.0

    def test_the_range_lands_on_the_real_text(self) -> None:
        doc, outcome = ground_one("Total 1,24O.00 due 2026-04-13")
        assert outcome.span is not None
        assert "1,240.00" in doc.text[outcome.span.start : outcome.span.end]

    def test_it_carries_a_page_and_geometry_like_an_exact_match(self) -> None:
        _, outcome = ground_one("Total 1,24O.00 due 2026-04-13")
        assert outcome.pages == (0,)
        assert outcome.geometry is not None


class TestBelowThresholdIsUngrounded:
    def test_a_fabricated_claim_is_ungrounded(self) -> None:
        _, outcome = ground_one("Chairman of the Interstellar Board of Directors")
        assert outcome.status is GroundingStatus.UNGROUNDED

    def test_it_carries_no_range_no_score_and_no_boxes(self) -> None:
        _, outcome = ground_one("Chairman of the Interstellar Board of Directors")
        assert outcome.span is None
        assert outcome.score is None
        assert outcome.geometry is None
        assert outcome.pages == ()

    def test_it_is_not_attached_to_the_nearest_available_range(self) -> None:
        """The failure mode FR-023 names: 'close enough' is not grounded."""
        _, outcome = ground_one("Total 9,999.99 due 1999-01-01")
        assert outcome.status is GroundingStatus.UNGROUNDED

    def test_no_exception_is_raised(self) -> None:
        """FR-045 -- ungrounded is an outcome, not an error."""
        _, outcome = ground_one("nothing like this appears anywhere in the text")
        assert outcome.status is GroundingStatus.UNGROUNDED


class TestClaimsWithNothingToSearchFor:
    def test_a_missing_claim_is_ungrounded(self) -> None:
        _, outcome = ground_one(None)
        assert outcome.status is GroundingStatus.UNGROUNDED

    def test_an_empty_claim_is_ungrounded(self) -> None:
        _, outcome = ground_one("")
        assert outcome.status is GroundingStatus.UNGROUNDED

    def test_a_whitespace_only_claim_is_ungrounded(self) -> None:
        """It would otherwise match everywhere, which would mean nothing (FR-026)."""
        _, outcome = ground_one("   \n\t ")
        assert outcome.status is GroundingStatus.UNGROUNDED


class TestTheThresholdIsNotAPostFilter:
    """GRD-19 — it changes which candidates are generated, not merely accepted."""

    def test_lowering_it_grounds_what_the_default_refuses(self) -> None:
        _, strict = ground_one("Total 9,999.99 due 1999-01-01")
        _, loose = ground_one("Total 9,999.99 due 1999-01-01", threshold=0.5)
        assert strict.status is GroundingStatus.UNGROUNDED
        assert loose.status is GroundingStatus.FUZZY

    def test_raising_it_refuses_what_the_default_grounds(self) -> None:
        _, default = ground_one("Total 1,24O.00 due 2026-04-13")
        _, strict = ground_one("Total 1,24O.00 due 2026-04-13", threshold=0.99)
        assert default.status is GroundingStatus.FUZZY
        assert strict.status is GroundingStatus.UNGROUNDED


class TestTheExactTierWins:
    """FR-021 — fuzzy runs only when there is no exact match at all."""

    def test_a_verbatim_claim_never_reports_fuzzy(self) -> None:
        _, outcome = ground_one("Total 1,240.00")
        assert outcome.status is GroundingStatus.EXACT
        assert outcome.score == 1.0

    def test_a_compound_word_broken_at_a_line_end_lands_at_exactly_the_threshold(self) -> None:
        """research.md R7's residual case, end to end rather than as arithmetic."""
        _, outcome = ground_one("well-known", text="A well-\nknown supplier of parts")
        assert outcome.status is GroundingStatus.FUZZY
        assert outcome.score == pytest.approx(0.900, abs=1e-9)
