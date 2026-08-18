"""T017 — the exact tier (GRD-13, GRD-14, FR-021, FR-025).

The tier that makes US1 a shippable product on its own: a claim present verbatim
resolves to where it is, and the other places it also appears are recorded rather
than thrown away.
"""

from __future__ import annotations

from docdoc.grounding import GroundingStatus, ground
from tests.support import make_document, make_extracted, make_extraction

TEXT = "Invoice INV-001\nTotal 1,240.00 due on 2026-04-13\nRemit 1,240.00 to ACME"


def ground_one(text: str, claim: str | None, **kw: object):
    doc = make_document(text)
    extraction = make_extraction(
        {"f": make_extracted("f", value="x", claimed_text=claim, **kw)}, document=doc
    )
    return doc, ground(doc, extraction)


class TestASingleOccurrence:
    def test_it_resolves_exactly_with_a_score_of_one(self) -> None:
        _, result = ground_one(TEXT, "Invoice INV-001")
        outcome = result.outcomes["f"]
        assert outcome.status is GroundingStatus.EXACT
        assert outcome.score == 1.0

    def test_the_range_reads_back_as_the_claim(self) -> None:
        doc, result = ground_one(TEXT, "Invoice INV-001")
        span = result.outcomes["f"].span
        assert span is not None
        assert doc.text[span.start : span.end] == "Invoice INV-001"

    def test_it_carries_pages_and_geometry(self) -> None:
        _, result = ground_one(TEXT, "Invoice INV-001")
        outcome = result.outcomes["f"]
        assert outcome.pages == (0,)
        assert outcome.geometry is not None
        assert len(outcome.geometry) > 0


class TestSeveralOccurrences:
    """GRD-13 — the earliest wins, the rest become alternatives at score 1.0."""

    def test_the_earliest_occurrence_wins(self) -> None:
        _, result = ground_one(TEXT, "1,240.00")
        span = result.outcomes["f"].span
        assert span is not None
        assert span.start == TEXT.index("1,240.00")

    def test_the_other_occurrences_are_recorded_as_alternatives(self) -> None:
        _, result = ground_one(TEXT, "1,240.00")
        alternatives = result.outcomes["f"].alternatives
        assert len(alternatives) == 1
        assert alternatives[0].span.start == TEXT.rindex("1,240.00")

    def test_every_exact_alternative_scores_one(self) -> None:
        _, result = ground_one(TEXT, "1,240.00")
        assert all(a.score == 1.0 for a in result.outcomes["f"].alternatives)

    def test_alternatives_are_capped_at_five(self) -> None:
        _, result = ground_one("ab " * 40, "ab")
        assert len(result.outcomes["f"].alternatives) == 5


class TestTheScoreIsStructural:
    """The exact tier does not run the fuzzy scorer to discover 1.0 (research.md R13).

    The scorer would also return 1.0, so the two agree today. Deriving one from
    the other would quietly make them the same quantity, and ADR-0004 says they
    are not comparable.
    """

    def test_an_exact_score_is_exactly_one_not_approximately(self) -> None:
        _, result = ground_one(TEXT, "ACME")
        assert result.outcomes["f"].score == 1.0
        assert isinstance(result.outcomes["f"].score, float)


class TestFoldingAppliesToBothSides:
    """FR-018 — a claim quoting what a human reads still matches the exact tier."""

    def test_a_ligature_in_the_source_still_matches_a_plain_claim(self) -> None:
        _, result = ground_one("The ﬁnal total is 99", "final total")
        assert result.outcomes["f"].status is GroundingStatus.EXACT

    def test_the_returned_range_points_at_the_unfolded_source(self) -> None:
        doc, result = ground_one("The ﬁnal total is 99", "final total")
        span = result.outcomes["f"].span
        assert span is not None
        assert "ﬁ" in doc.text[span.start : span.end]


class TestTheClaimIsNeverAltered:
    """FR-011 — folding happens on a copy; the recorded claim stays byte-identical."""

    def test_the_extraction_result_keeps_its_claim_verbatim(self) -> None:
        # Escapes rather than literal characters: a NBSP and a regular space are
        # indistinguishable in a source file, and a test asserting the difference
        # between them has to be readable to be worth anything.
        doc = make_document("Amount\u00a0due\u00a01,240.00")
        claim = "Amount due 1,240.00"
        extraction = make_extraction(
            {"f": make_extracted("f", value="x", claimed_text=claim)}, document=doc
        )
        result = ground(doc, extraction)

        # It grounds, because both sides were folded before comparing...
        assert result.outcomes["f"].status is GroundingStatus.EXACT
        # ...and the stored claim is untouched, plain spaces and all.
        assert extraction.values["f"].claimed_text == claim
        assert "\u00a0" not in extraction.values["f"].claimed_text

    def test_the_returned_range_points_at_the_unfolded_source(self) -> None:
        doc = make_document("Amount\u00a0due\u00a01,240.00")
        extraction = make_extraction(
            {"f": make_extracted("f", value="x", claimed_text="Amount due 1,240.00")},
            document=doc,
        )
        span = ground(doc, extraction).outcomes["f"].span
        assert span is not None
        assert "\u00a0" in doc.text[span.start : span.end]
