"""T063 — the edge cases spec.md enumerates, defended rather than assumed.

**Every case here was verified to behave correctly before this file existed.**
That is worth stating up front: nothing below is a bug fix, and a reader who
comes here looking for the defect will not find one. The spec listed these as
the boundaries the feature has to hold at, `/speckit-converge` checked that it
does, and this file is what stops that from being a one-time observation.

The distinction matters for how the file reads. A test written after a failure
argues with the code; these agree with it, and their job is to notice if that
ever stops being true.
"""

from __future__ import annotations

import pytest

from docdoc.grounding import GroundingOptions, GroundingStatus, ground
from docdoc.grounding.match import MAX_ALTERNATIVES
from tests.support import make_document, make_extracted, make_extraction


def resolve(text: str, claim: str | None, *, doc=None, **opts: object):
    document = doc if doc is not None else make_document(text)
    extraction = make_extraction(
        {"f": make_extracted("f", value="x", claimed_text=claim)}, document=document
    )
    options = GroundingOptions(**opts) if opts else None
    return document, ground(document, extraction, options=options).outcomes["f"]


class TestDegenerateDocuments:
    """Nothing to search in. Each must be ungrounded, and none may raise."""

    def test_an_empty_document(self) -> None:
        _, outcome = resolve("", "anything")
        assert outcome.status is GroundingStatus.UNGROUNDED
        assert outcome.span is None

    def test_a_whitespace_only_document(self) -> None:
        _, outcome = resolve("   \n\t  ", "anything")
        assert outcome.status is GroundingStatus.UNGROUNDED

    def test_a_whitespace_only_document_against_a_whitespace_claim(self) -> None:
        """Both sides fold to a single space, and a space must still match nothing.

        The near-miss here is real: fold both and you get `' '` on each side, so a
        naive implementation reports an exact match on a document containing no
        text at all. FR-026 forbids searching for a claim that would match
        everywhere, which is what keeps this ungrounded.
        """
        _, outcome = resolve("   \n\t  ", "  ")
        assert outcome.status is GroundingStatus.UNGROUNDED


class TestClaimLength:
    def test_a_claim_longer_than_the_whole_document(self) -> None:
        _, outcome = resolve("short", "a much longer claim than the document holds")
        assert outcome.status is GroundingStatus.UNGROUNDED

    def test_a_claim_exactly_as_long_as_the_document(self) -> None:
        text = "Invoice INV-001"
        _, outcome = resolve(text, text)
        assert outcome.status is GroundingStatus.EXACT
        assert outcome.span is not None
        assert (outcome.span.start, outcome.span.end) == (0, len(text))

    def test_a_single_character_claim(self) -> None:
        """`k = 0` at this length, so the filter degenerates to exact search."""
        _, outcome = resolve("Total 1,240.00", "T")
        assert outcome.status is GroundingStatus.EXACT


class TestMultiPage:
    def test_a_claim_resolving_across_a_page_boundary_reports_both_pages(self) -> None:
        text = "Invoice total\n1,240.00 due now"
        doc = make_document(text, page_breaks=(14,))
        _, outcome = resolve(text, "total\n1,240.00", doc=doc)
        assert outcome.status is GroundingStatus.EXACT
        assert outcome.pages == (0, 1)

    def test_a_claim_inside_one_page_reports_one_page(self) -> None:
        text = "Invoice total\n1,240.00 due now"
        doc = make_document(text, page_breaks=(14,))
        _, outcome = resolve(text, "due now", doc=doc)
        assert outcome.pages == (1,)


class TestScriptsAndPlanes:
    """Text a naive character count and a real position disagree about."""

    def test_characters_outside_the_basic_multilingual_plane(self) -> None:
        _, outcome = resolve("Total 😀 1,240.00", "Total 😀 1,240.00")
        assert outcome.status is GroundingStatus.EXACT

    def test_the_range_after_an_astral_character_is_still_correct(self) -> None:
        """The case a UTF-16 offset model gets wrong and Python's does not."""
        text = "😀 Total 1,240.00"
        doc, outcome = resolve(text, "1,240.00")
        assert outcome.span is not None
        assert doc.text[outcome.span.start : outcome.span.end] == "1,240.00"

    def test_a_right_to_left_script(self) -> None:
        _, outcome = resolve("المبلغ 1,240.00", "المبلغ 1,240.00")
        assert outcome.status is GroundingStatus.EXACT

    def test_mixed_scripts_within_one_claim(self) -> None:
        text = "Invoice المبلغ 1,240.00 EUR"
        doc, outcome = resolve(text, "المبلغ 1,240.00")
        assert outcome.status is GroundingStatus.EXACT
        assert outcome.span is not None
        assert doc.text[outcome.span.start : outcome.span.end] == "المبلغ 1,240.00"


class TestHighCardinality:
    def test_a_claim_occurring_hundreds_of_times_still_caps_its_alternatives(self) -> None:
        _, outcome = resolve("EUR " * 300, "EUR")
        assert outcome.status is GroundingStatus.EXACT
        assert len(outcome.alternatives) == MAX_ALTERNATIVES

    def test_the_winner_is_still_the_earliest_of_the_three_hundred(self) -> None:
        _, outcome = resolve("EUR " * 300, "EUR")
        assert outcome.span is not None
        assert outcome.span.start == 0

    def test_a_header_repeated_on_every_page(self) -> None:
        """The claim is real, appears three times, and one of them has to win."""
        text = "ACME LTD\n" * 3 + "Total 1,240.00"
        _, outcome = resolve(text, "ACME LTD")
        assert outcome.span is not None
        assert outcome.span.start == 0
        assert len(outcome.alternatives) == 2


class TestThresholdBoundaries:
    """0.0 and 1.0 are both legal, and both change every outcome."""

    def test_a_threshold_of_one_admits_only_what_the_exact_tier_found(self) -> None:
        _, near_miss = resolve("Total 1,240.00", "Total 1,24O.00", threshold=1.0)
        assert near_miss.status is GroundingStatus.UNGROUNDED

    def test_a_threshold_of_one_leaves_an_exact_match_alone(self) -> None:
        _, exact = resolve("Total 1,240.00", "Total 1,240.00", threshold=1.0)
        assert exact.status is GroundingStatus.EXACT

    def test_a_threshold_of_zero_grounds_almost_anything(self) -> None:
        _, outcome = resolve("Total 1,240.00", "Total 9,999.99", threshold=0.0)
        assert outcome.status is GroundingStatus.FUZZY
        assert outcome.score is not None

    def test_neither_extreme_raises(self) -> None:
        for threshold in (0.0, 1.0):
            _, outcome = resolve("Total 1,240.00", "nothing alike", threshold=threshold)
            assert outcome.status in tuple(GroundingStatus)

    def test_an_out_of_range_threshold_is_rejected_at_construction(self) -> None:
        """Legal is 0.0..1.0; outside that the option is refused, not clamped."""
        from pydantic import ValidationError

        for bad in (-0.1, 1.1):
            with pytest.raises(ValidationError):
                GroundingOptions(threshold=bad)


class TestExtractionShapes:
    def test_an_extraction_with_zero_values(self) -> None:
        doc = make_document("Invoice INV-001")
        result = ground(doc, make_extraction({}, document=doc))
        assert result.outcomes == {}
        assert result.counts.grounding_rate is None
        assert result.artifact_id.startswith("sha256:")

    def test_an_extraction_where_every_value_is_absent(self) -> None:
        doc = make_document("Invoice INV-001")
        values = {f"f{i}": make_extracted(f"f{i}", present=False) for i in range(5)}
        result = ground(doc, make_extraction(values, document=doc))
        assert result.outcomes == {}
        assert result.counts.not_applicable == 5
        assert result.counts.grounding_rate is None
