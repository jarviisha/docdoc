"""T039 — the untrusted signal changes nothing (SC-017, FR-031, FR-032, FR-033).

ADR-0004's rule is that `model_confidence` routes nothing and gates nothing. That
is easy to state and easy to violate accidentally -- a tie-break that consulted
it, a threshold that scaled by it, an ordering that happened to read it. The
mechanical form of the rule is this: ground the same set twice with that number
altered, and every byte of both results must agree.
"""

from __future__ import annotations

from docdoc.grounding import ground
from tests.support import make_document, make_extracted, make_extraction

TEXT = "ACME SUPPLIES\nInvoice INV-001\nTotal 1,240.00 and 1,240.00 again"


def build(confidence: float | None):
    doc = make_document(TEXT)
    extraction = make_extraction(
        {
            "exact": make_extracted(
                "exact", value="x", claimed_text="INV-001", model_confidence=confidence
            ),
            "fuzzy": make_extracted(
                "fuzzy", value="x", claimed_text="Total 1,24O.00", model_confidence=confidence
            ),
            "ungrounded": make_extracted(
                "ungrounded", value="x", claimed_text="nowhere at all", model_confidence=confidence
            ),
            "ambiguous": make_extracted(
                "ambiguous", value="x", claimed_text="1,240.00", model_confidence=confidence
            ),
        },
        document=doc,
    )
    return doc, extraction


class TestItInfluencesNothing:
    def test_outcomes_are_identical_however_confident_the_model_claimed_to_be(self) -> None:
        results = [ground(*build(c)) for c in (None, 0.0, 0.5, 1.0)]
        first = results[0]
        for other in results[1:]:
            assert other.outcomes == first.outcomes

    def test_statuses_scores_and_spans_all_agree(self) -> None:
        low = ground(*build(0.01))
        high = ground(*build(0.99))
        for path in low.outcomes:
            a, b = low.outcomes[path], high.outcomes[path]
            assert (a.status, a.score, a.span) == (b.status, b.score, b.span)

    def test_alternatives_agree(self) -> None:
        low = ground(*build(0.01))
        high = ground(*build(0.99))
        assert low.outcomes["ambiguous"].alternatives == high.outcomes["ambiguous"].alternatives

    def test_the_artifact_id_does_not_move(self) -> None:
        """It is not a grounding input, so it must not reach the options hash."""
        assert ground(*build(0.01)).artifact_id == ground(*build(0.99)).artifact_id

    def test_counts_agree(self) -> None:
        assert ground(*build(None)).counts == ground(*build(1.0)).counts


class TestItIsPassedThroughUntouched:
    def test_the_extraction_keeps_the_number_the_model_reported(self) -> None:
        doc, extraction = build(0.42)
        ground(doc, extraction)
        assert extraction.values["exact"].model_confidence == 0.42


class TestTheReservedCalibrationFieldsStayUnset:
    """FR-032, FR-033 -- ADR-0004 reserves them for a calibrator that does not exist.

    Same family as `model_confidence`: extraction-layer fields this stage must
    leave alone. A blended score may only ever be produced by a versioned
    calibrator writing to them, never by grounding filling them in because it
    happened to have a number.
    """

    def test_grounding_does_not_populate_them(self) -> None:
        doc, extraction = build(0.9)
        ground(doc, extraction)
        for value in extraction.values.values():
            assert value.calibrated_confidence is None
            assert value.calibrator_version is None

    def test_no_grounding_outcome_exposes_a_blended_score(self) -> None:
        doc, extraction = build(0.9)
        result = ground(doc, extraction)
        fields = set(next(iter(result.outcomes.values())).model_dump())
        assert "calibrated_confidence" not in fields
        assert "model_confidence" not in fields
