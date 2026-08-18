"""T040 — no view position and no view text ever leaves this layer (FR-016, FR-013).

ADR-0006's headline failure is a range that is valid in the *view* being returned
as though it were valid in the source. On a document where the two differ in
length, such a range still looks perfectly well formed -- it just points
somewhere else. This file grounds against exactly that kind of document, so a
missing offset-map hop would show up as a wrong range rather than as an exception.

The second half covers FR-013's other clause: the folded text itself is never
returned, never in a result, and never in a log. The offset assertions above
cover positions and `test_observe` covers log lines; neither covers the API
surface, which is what a caller would actually reach for.
"""

from __future__ import annotations

import logging

from docdoc.grounding import ground
from docdoc.grounding.view import MatchView
from tests.support import make_document, make_extracted, make_extraction

# Ligatures expand and combining marks compose, so the view is a different length
# than the source in both directions -- a view offset used as a source offset
# lands in the wrong place rather than raising.
TEXT = "Société ﬁnale\nInvoice ﬃliation INV-001\nTotal 1,240.00 and 1,240.00"


def build():
    doc = make_document(TEXT)
    extraction = make_extraction(
        {
            "a": make_extracted("a", value="x", claimed_text="INV-001"),
            "b": make_extracted("b", value="x", claimed_text="1,240.00"),
            "c": make_extracted("c", value="x", claimed_text="Société finale"),
            "d": make_extracted("d", value="x", claimed_text="Total 1,24O.00"),
        },
        document=doc,
    )
    return doc, extraction


class TestTheViewAndSourceReallyDiffer:
    def test_the_premise_holds(self) -> None:
        """If this fails the rest of the file proves nothing."""
        doc = make_document(TEXT)
        view = MatchView.build(doc)
        assert len(view.text) != len(doc.text)


class TestEveryRangeIsASourceRange:
    def test_every_winning_span_fits_the_source_text(self) -> None:
        doc, extraction = build()
        for outcome in ground(doc, extraction).outcomes.values():
            if outcome.span is None:
                continue
            assert 0 <= outcome.span.start <= outcome.span.end <= len(doc.text)

    def test_every_alternative_span_fits_the_source_text(self) -> None:
        doc, extraction = build()
        for outcome in ground(doc, extraction).outcomes.values():
            for alt in outcome.alternatives:
                assert 0 <= alt.span.start <= alt.span.end <= len(doc.text)

    def test_every_span_reads_back_as_something_that_folds_to_the_claim(self) -> None:
        """The assertion a view offset masquerading as a source offset would fail.

        Asserted per tier, because the two mean different things: an exact match
        must read back containing the claim, while a fuzzy one must read back
        *similar* to it -- it did not match verbatim, which is why it is fuzzy.
        Demanding containment of both would be asserting the tiers are the same.
        """
        from rapidfuzz.distance import Levenshtein

        from docdoc.grounding import GroundingStatus
        from docdoc.grounding.options import DEFAULT_THRESHOLD
        from docdoc.grounding.view import fold_claim

        doc, extraction = build()
        result = ground(doc, extraction)
        for path, outcome in result.outcomes.items():
            if outcome.span is None:
                continue
            read_back = fold_claim(doc.text[outcome.span.start : outcome.span.end])
            claim = fold_claim(extraction.values[path].claimed_text or "")
            if outcome.status is GroundingStatus.EXACT:
                assert claim in read_back, path
            else:
                similarity = Levenshtein.normalized_similarity(claim, read_back)
                assert similarity >= DEFAULT_THRESHOLD, (path, similarity)

    def test_spans_survive_the_kernel_own_validation(self) -> None:
        doc, extraction = build()
        for outcome in ground(doc, extraction).outcomes.values():
            if outcome.span is not None:
                outcome.span.validate_within(len(doc.text))


class TestTheViewIsNeverExposed:
    def test_no_public_export_returns_the_folded_text(self) -> None:
        import docdoc.grounding as pkg

        assert "MatchView" not in pkg.__all__
        assert "fold_claim" not in pkg.__all__
        assert not hasattr(pkg, "MatchView")

    def test_no_result_field_carries_the_folded_text(self) -> None:
        doc, extraction = build()
        result = ground(doc, extraction)
        view = MatchView.build(doc)
        blob = result.model_dump_json()
        assert view.text not in blob
        assert doc.text not in blob

    def test_no_log_record_carries_the_folded_text(self, caplog) -> None:
        doc, extraction = build()
        view = MatchView.build(doc)
        with caplog.at_level(logging.DEBUG, logger="docdoc.grounding"):
            ground(doc, extraction)
        for record in caplog.records:
            rendered = str(getattr(record, "docdoc", "")) + record.getMessage()
            assert view.text not in rendered
            assert doc.text not in rendered

    def test_an_error_message_carries_no_view_content(self) -> None:
        import pytest

        from docdoc.grounding import GroundingError

        doc = make_document(TEXT, data=b"%PDF-1.7 one")
        other = make_document(TEXT, data=b"%PDF-1.7 two")
        extraction = make_extraction(
            {"a": make_extracted("a", value="x", claimed_text="INV-001")}, document=other
        )
        view = MatchView.build(doc)
        with pytest.raises(GroundingError) as caught:
            ground(doc, extraction)
        assert view.text not in str(caught.value)
        assert doc.text not in str(caught.value)
