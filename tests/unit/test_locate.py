"""T027 — Document.locate (US1, FR-008, FR-009, FR-022).

Resolving a text range to physical locations is the reason the project exists;
everything above it depends on this being exactly right.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from docdoc.kernel import CapabilityError, Geometry, Span, SpanError
from tests.support import make_document

# "Invoice No: INV-001\nTotal: 125000"
#  0123456789...        tokens: Invoice(0,7) No:(8,11) INV-001(12,19)
#                               Total:(20,26) 125000(27,33)
TEXT = "Invoice No: INV-001\nTotal: 125000"


class TestBasicResolution:
    def test_a_value_resolves_to_a_page_and_a_box(self) -> None:
        doc = make_document(TEXT)
        (span,) = doc.find("INV-001")
        geometries = doc.locate(span)

        assert len(geometries) == 1
        assert geometries[0].page_index == 0
        box = geometries[0].bbox
        assert 0.0 <= box.x0 <= box.x1 <= 1.0
        assert 0.0 <= box.y0 <= box.y1 <= 1.0

    def test_the_box_belongs_to_the_token_that_produced_the_text(self) -> None:
        doc = make_document(TEXT)
        (span,) = doc.find("INV-001")
        token = next(t for t in doc.tokens if t.span == span)
        assert doc.locate(span) == (token.geometry,)

    def test_returns_one_entry_per_intersecting_token(self) -> None:
        """No grouping, union, or line detection (research.md R8)."""
        doc = make_document(TEXT)
        geometries = doc.locate(Span(0, 19))  # Invoice, No:, INV-001
        assert len(geometries) == 3

    def test_partial_overlap_returns_the_whole_token_box(self) -> None:
        """No sub-token interpolation -- parsers report geometry per token,
        and interpolating would assume uniform glyph advance (research.md R7).
        """
        doc = make_document(TEXT)
        token = next(t for t in doc.tokens if t.span == Span(0, 7))
        assert doc.locate(Span(2, 4)) == (token.geometry,)


class TestOrdering:
    def test_results_are_ordered_by_page_then_position(self) -> None:
        doc = make_document(TEXT, page_breaks=(20,))
        geometries = doc.locate(Span(0, len(TEXT)))
        keys = [(g.page_index, g.bbox.x0) for g in geometries]
        assert keys == sorted(keys)

    def test_a_multi_page_span_returns_entries_from_both_pages(self) -> None:
        doc = make_document(TEXT, page_breaks=(20,))
        geometries = doc.locate(Span(12, 26))  # INV-001 on p0, Total: on p1
        assert {g.page_index for g in geometries} == {0, 1}


class TestEmptyAndAbsent:
    def test_a_zero_length_span_resolves_to_nothing(self) -> None:
        """FR-009 — an empty result, not an error."""
        assert make_document(TEXT).locate(Span(5, 5)) == ()

    def test_a_span_covering_only_whitespace_resolves_to_nothing(self) -> None:
        """Position 7 is the space between tokens; no token claims it."""
        assert make_document(TEXT).locate(Span(7, 8)) == ()

    def test_an_empty_document_resolves_nothing(self) -> None:
        doc = make_document("")
        assert doc.locate(Span(0, 0)) == ()


class TestErrors:
    def test_a_span_past_the_end_of_text_is_an_error(self) -> None:
        """No clamping, no truncation, no empty stand-in (US1 scenario 3)."""
        doc = make_document(TEXT)
        with pytest.raises(SpanError) as excinfo:
            doc.locate(Span(0, len(TEXT) + 1))
        assert excinfo.value.text_length == len(TEXT)

    def test_a_reversed_span_is_an_error(self) -> None:
        with pytest.raises(SpanError):
            make_document(TEXT).locate(Span(9, 2))

    def test_geometry_unavailable_raises_rather_than_returning_empty(self) -> None:
        """FR-022 — the no-silent-fallback rule.

        Returning () here would be indistinguishable from "no token there",
        which is precisely the confusion the constitution forbids.
        """
        doc = make_document(TEXT, with_geometry=False)
        with pytest.raises(CapabilityError) as excinfo:
            doc.locate(Span(12, 19))
        assert excinfo.value.capability == "geometry"
        assert excinfo.value.available is False
        assert excinfo.value.parser_id == doc.provenance.parser_id

    def test_capability_error_is_raised_even_for_an_empty_span(self) -> None:
        """The capability is absent regardless of what was asked for."""
        doc = make_document(TEXT, with_geometry=False)
        with pytest.raises(CapabilityError):
            doc.locate(Span(3, 3))


class TestPurity:
    def test_locate_does_not_mutate_the_document(self) -> None:
        doc = make_document(TEXT)
        before = (doc.id, doc.text, len(doc.tokens))
        doc.locate(Span(0, 19))
        assert (doc.id, doc.text, len(doc.tokens)) == before

    def test_repeated_calls_return_equal_results(self) -> None:
        doc = make_document(TEXT)
        assert doc.locate(Span(0, 19)) == doc.locate(Span(0, 19))

    def test_the_document_cannot_be_mutated(self) -> None:
        """FR-002 — immutability, asserted from the caller's side."""
        doc = make_document(TEXT)
        with pytest.raises(ValidationError):
            doc.text = "tampered"  # type: ignore[misc]


class TestPageResolution:
    """Page lookup must work even when geometry is unavailable.

    Addresses analysis finding G1: FR-006 requires every token be traceable to a
    page, but geometry is the only page-bearing field, and locate() refuses to
    answer when geometry is absent. page_for() closes that hole.
    """

    def test_page_for_resolves_a_span_to_its_pages(self) -> None:
        doc = make_document(TEXT, page_breaks=(20,))
        assert doc.page_for(Span(12, 19)) == (0,)
        assert doc.page_for(Span(20, 26)) == (1,)

    def test_page_for_spans_multiple_pages(self) -> None:
        doc = make_document(TEXT, page_breaks=(20,))
        assert doc.page_for(Span(12, 26)) == (0, 1)

    def test_page_for_works_without_geometry(self) -> None:
        doc = make_document(TEXT, page_breaks=(20,), with_geometry=False)
        assert doc.page_for(Span(12, 19)) == (0,)

    def test_page_for_rejects_an_out_of_range_span(self) -> None:
        with pytest.raises(SpanError):
            make_document(TEXT).page_for(Span(0, len(TEXT) + 5))

    def test_page_for_an_empty_span_returns_nothing(self) -> None:
        assert make_document(TEXT).page_for(Span(5, 5)) == ()


def test_locate_returns_geometry_instances() -> None:
    doc = make_document(TEXT)
    assert all(isinstance(g, Geometry) for g in doc.locate(Span(0, 19)))
