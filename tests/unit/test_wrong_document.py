"""T020 — grounding refuses a document the extraction did not come from (FR-002).

The failure this prevents is the one that most looks like success. Ranges anchor
to a specific parse (ADR-0002), so resolving one parse's claims against another
returns ranges that are structurally valid, land on real tokens, and produce real
bounding boxes -- pointing into the wrong document. Nothing downstream could tell.

The test that matters most here is the second class: refusal must hold **even
when the other document contains the claims**, because that is exactly the case
where silently proceeding would look correct.
"""

from __future__ import annotations

import pytest

from docdoc.grounding import GroundingError, ground
from tests.support import make_document, make_extracted, make_extraction

TEXT = "Invoice INV-001 total 1,240.00"


class TestRefusal:
    def test_a_mismatched_document_raises(self) -> None:
        doc = make_document(TEXT, data=b"%PDF-1.7 one")
        other = make_document("Something else entirely", data=b"%PDF-1.7 two")
        extraction = make_extraction(
            {"f": make_extracted("f", value="x", claimed_text="INV-001")}, document=other
        )
        with pytest.raises(GroundingError):
            ground(doc, extraction)

    def test_the_error_names_both_identities(self) -> None:
        doc = make_document(TEXT, data=b"%PDF-1.7 one")
        other = make_document("Something else", data=b"%PDF-1.7 two")
        extraction = make_extraction(
            {"f": make_extracted("f", value="x", claimed_text="INV-001")}, document=other
        )
        with pytest.raises(GroundingError) as excinfo:
            ground(doc, extraction)
        assert excinfo.value.document_id == doc.id
        assert excinfo.value.extraction_document_id == other.id
        message = str(excinfo.value)
        assert doc.id in message
        assert other.id in message

    def test_it_refuses_even_when_the_other_document_contains_the_claims(self) -> None:
        """The case where proceeding would look entirely correct.

        Both documents contain "INV-001", so grounding would succeed and return a
        plausible range. That it refuses anyway is the whole requirement.
        """
        doc = make_document(TEXT, data=b"%PDF-1.7 one")
        twin = make_document(TEXT, data=b"%PDF-1.7 two")
        assert doc.text == twin.text
        assert doc.id != twin.id
        extraction = make_extraction(
            {"f": make_extracted("f", value="x", claimed_text="INV-001")}, document=twin
        )
        with pytest.raises(GroundingError):
            ground(doc, extraction)

    def test_a_different_parse_of_the_same_bytes_is_still_refused(self) -> None:
        """Same file, different parser options -- a different parse, so different ranges."""
        doc = make_document(TEXT, options={"dpi": 200})
        other = make_document(TEXT, options={"dpi": 300})
        assert doc.source.blob_id == other.source.blob_id
        assert doc.id != other.id
        extraction = make_extraction(
            {"f": make_extracted("f", value="x", claimed_text="INV-001")}, document=other
        )
        with pytest.raises(GroundingError):
            ground(doc, extraction)

    def test_no_outcome_is_produced_when_it_refuses(self) -> None:
        doc = make_document(TEXT, data=b"%PDF-1.7 one")
        twin = make_document(TEXT, data=b"%PDF-1.7 two")
        extraction = make_extraction(
            {"f": make_extracted("f", value="x", claimed_text="INV-001")}, document=twin
        )
        with pytest.raises(GroundingError):
            ground(doc, extraction)


class TestTheMatchingCaseStillWorks:
    def test_the_right_document_grounds_normally(self) -> None:
        doc = make_document(TEXT)
        extraction = make_extraction(
            {"f": make_extracted("f", value="x", claimed_text="INV-001")}, document=doc
        )
        assert ground(doc, extraction).outcomes["f"].span is not None


class TestOffsetMapFailuresNameWhereTheyHappened:
    """FR-043 — a typed error carrying enough detail to identify what failed.

    These are defensive guards that should never fire, which is exactly why they
    have to be legible when they do: a map failure is the one error meaning "the
    highest-risk component in the grounding path broke". "offset map is not
    contiguous" without saying which document is a bug report nobody can act on.
    """

    def test_a_malformed_map_names_its_document(self) -> None:
        from docdoc.grounding.offsets import OffsetMap, Segment

        with pytest.raises(GroundingError) as caught:
            OffsetMap(
                (Segment(0, 0, 5, 5, identity=True),),
                view_len=99,
                source_len=5,
                document_id="sha256:" + "a" * 64,
            )
        assert caught.value.document_id == "sha256:" + "a" * 64

    def test_a_map_built_for_a_document_carries_that_document(self) -> None:
        from docdoc.grounding.view import MatchView

        doc = make_document(TEXT)
        assert MatchView.build(doc).offsets.document_id == doc.id

    def test_a_map_built_for_a_claim_carries_no_document(self) -> None:
        """A claim has no document, so the field is optional rather than a lie."""
        from docdoc.grounding.view import _fold

        assert _fold("just a claim")[1].document_id is None

    def test_a_mapping_failure_names_the_value_it_was_resolving(self) -> None:
        """The document comes from the map; the field path comes from this layer."""
        from docdoc.grounding.match import Candidate, outcome_for
        from docdoc.grounding.view import MatchView

        doc = make_document(TEXT)
        view = MatchView.build(doc)
        with pytest.raises(GroundingError) as caught:
            outcome_for(
                field_path="line_items[3].amount",
                document=doc,
                view=view,
                winner=Candidate(1.0, 0, len(view.text) + 50),
                runners_up=[],
            )
        assert caught.value.field_path == "line_items[3].amount"
        assert caught.value.document_id == doc.id
        assert "line_items[3].amount" in str(caught.value)
