"""T018 — a value without geometry is still grounded (FR-006, GRD-17).

The distinction this file defends is between two facts a single empty tuple
would collapse:

    geometry is None   the parser supplied none -- unavailable
    geometry == ()     geometry exists; this range covers no tokens

A caller that cannot tell them apart reads "unavailable" as "nothing is there",
which is the misreading FR-006 exists to prevent. The kernel makes the same
distinction one level down: `locate()` raises rather than returning `()`.
"""

from __future__ import annotations

from docdoc.grounding import GroundingStatus, ground
from tests.support import make_document, make_extracted, make_extraction

TEXT = "Invoice INV-001 total 1,240.00"


def ground_claim(doc, claim: str):
    extraction = make_extraction(
        {"f": make_extracted("f", value="x", claimed_text=claim)}, document=doc
    )
    return ground(doc, extraction).outcomes["f"]


class TestParserSuppliedNoGeometry:
    def test_the_value_is_still_grounded(self) -> None:
        doc = make_document(TEXT, with_geometry=False)
        assert ground_claim(doc, "INV-001").status is GroundingStatus.EXACT

    def test_it_still_carries_a_range(self) -> None:
        doc = make_document(TEXT, with_geometry=False)
        outcome = ground_claim(doc, "INV-001")
        assert outcome.span is not None
        assert doc.text[outcome.span.start : outcome.span.end] == "INV-001"

    def test_it_still_carries_a_page(self) -> None:
        """Pages tile the text exactly, so they survive a parser with no geometry."""
        doc = make_document(TEXT, with_geometry=False)
        assert ground_claim(doc, "INV-001").pages == (0,)

    def test_geometry_is_none_and_not_an_empty_tuple(self) -> None:
        doc = make_document(TEXT, with_geometry=False)
        outcome = ground_claim(doc, "INV-001")
        assert outcome.geometry is None
        assert outcome.geometry != ()


class TestParserSuppliedGeometry:
    def test_geometry_is_a_tuple_of_boxes(self) -> None:
        doc = make_document(TEXT, with_geometry=True)
        outcome = ground_claim(doc, "INV-001")
        assert outcome.geometry is not None
        assert len(outcome.geometry) >= 1

    def test_the_two_absences_are_distinguishable(self) -> None:
        """The whole point, stated as one assertion a reader can check."""
        with_geo = ground_claim(make_document(TEXT, with_geometry=True), "INV-001")
        without = ground_claim(make_document(TEXT, with_geometry=False), "INV-001")
        assert with_geo.geometry is not None
        assert without.geometry is None
        assert with_geo.status is without.status is GroundingStatus.EXACT
