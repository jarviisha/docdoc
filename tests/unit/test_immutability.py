"""T019 — grounding reads and never writes (FR-007).

Asserted by comparing before and after rather than by trusting that nothing in
the layer writes. The document is frozen, so a mutation would raise -- but the
canonical text is the thing every range in the system is interpreted against, and
"we would have noticed" is not the standard Principle I sets for it.
"""

from __future__ import annotations

from docdoc.grounding import ground
from tests.support import make_document, make_extracted, make_extraction

TEXT = "Invoice INV-001\nTotal 1,240.00"


def build():
    doc = make_document(TEXT)
    extraction = make_extraction(
        {
            "number": make_extracted("number", value="INV-001", claimed_text="INV-001"),
            "missing": make_extracted("missing", present=False),
            "bogus": make_extracted("bogus", value="x", claimed_text="nowhere at all"),
        },
        document=doc,
    )
    return doc, extraction


class TestTheDocumentIsUntouched:
    def test_canonical_text_is_byte_identical(self) -> None:
        doc, extraction = build()
        before = doc.text
        ground(doc, extraction)
        assert doc.text == before
        assert doc.text == TEXT

    def test_identity_provenance_and_pages_are_unchanged(self) -> None:
        doc, extraction = build()
        before = (doc.id, doc.provenance, doc.pages, len(doc.tokens), doc.source)
        ground(doc, extraction)
        assert (doc.id, doc.provenance, doc.pages, len(doc.tokens), doc.source) == before


class TestTheExtractionResultIsUntouched:
    def test_claims_and_values_survive_verbatim(self) -> None:
        _, extraction = build()
        doc = make_document(TEXT)
        extraction = make_extraction(dict(extraction.values), document=doc)
        before = {k: (v.value, v.claimed_text, v.present) for k, v in extraction.values.items()}
        ground(doc, extraction)
        after = {k: (v.value, v.claimed_text, v.present) for k, v in extraction.values.items()}
        assert after == before

    def test_the_grounding_fields_milestone_three_left_unresolved_stay_unresolved(self) -> None:
        """Grounding produces a new result; it does not backfill the old one.

        The alternative design -- writing the resolved status onto the
        ExtractedValue -- would mutate the extraction artifact after its identity
        was computed, so the stored result would no longer hash to its own id.
        """
        doc, extraction = build()
        ground(doc, extraction)
        assert extraction.values["number"].grounding is None
        assert extraction.values["number"].grounding_score is None

    def test_provenance_and_artifact_id_are_unchanged(self) -> None:
        doc, extraction = build()
        before = (extraction.artifact_id, extraction.provenance)
        ground(doc, extraction)
        assert (extraction.artifact_id, extraction.provenance) == before


class TestGroundingTwiceIsSideEffectFree:
    def test_two_runs_produce_equal_results(self) -> None:
        doc, extraction = build()
        first = ground(doc, extraction)
        second = ground(doc, extraction)
        assert first.artifact_id == second.artifact_id
        assert first.outcomes == second.outcomes
        assert first.counts == second.counts
