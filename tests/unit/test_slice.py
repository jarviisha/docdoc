"""T034 — Document.slice (US2, FR-010)."""

from __future__ import annotations

import pytest

from docdoc.kernel import Span, SpanError
from tests.support import make_document

TEXT = "Invoice No: INV-001\nTotal: 125000"
# tokens: Invoice(0,7) No:(8,11) INV-001(12,19) Total:(20,26) 125000(27,33)


class TestText:
    def test_sliced_text_equals_the_original_range(self) -> None:
        doc = make_document(TEXT)
        assert doc.slice(Span(12, 19)).text == TEXT[12:19]

    def test_slicing_everything_reproduces_the_text(self) -> None:
        doc = make_document(TEXT)
        assert doc.slice(Span(0, len(TEXT))).text == TEXT


class TestTokens:
    def test_fully_contained_tokens_are_retained_and_rebased(self) -> None:
        doc = make_document(TEXT)
        part = doc.slice(Span(12, 19))
        assert len(part.tokens) == 1
        assert part.tokens[0].span == Span(0, 7)
        assert part.text[0:7] == "INV-001"

    def test_partially_covered_tokens_are_dropped(self) -> None:
        """Keeping a clipped token would leave its geometry describing glyphs
        that are no longer present -- a silently wrong box.
        """
        doc = make_document(TEXT)
        part = doc.slice(Span(14, 19))  # cuts into the middle of INV-001
        assert len(part.tokens) == 0

    def test_geometry_is_carried_through_unchanged(self) -> None:
        """The property that makes the round-trip invariant hold."""
        doc = make_document(TEXT)
        original = next(t for t in doc.tokens if t.span == Span(12, 19))
        part = doc.slice(Span(12, 19))
        assert part.tokens[0].geometry == original.geometry


class TestPages:
    def test_page_numbers_are_preserved_not_renumbered(self) -> None:
        """A slice of page 1 still reports page 1 (design decision, plan.md)."""
        doc = make_document(TEXT, page_breaks=(20,))
        part = doc.slice(Span(20, len(TEXT)))
        assert [p.index for p in part.pages] == [1]

    def test_page_spans_are_clipped_and_rebased(self) -> None:
        doc = make_document(TEXT, page_breaks=(20,))
        part = doc.slice(Span(20, len(TEXT)))
        assert part.pages[0].span == Span(0, len(TEXT) - 20)

    def test_a_span_crossing_a_page_boundary_keeps_both_pages(self) -> None:
        doc = make_document(TEXT, page_breaks=(20,))
        part = doc.slice(Span(12, 26))
        assert [p.index for p in part.pages] == [0, 1]

    def test_tokens_keep_their_page_association(self) -> None:
        doc = make_document(TEXT, page_breaks=(20,))
        part = doc.slice(Span(12, 26))
        pages_used = {t.geometry.page_index for t in part.tokens if t.geometry}
        assert pages_used == {0, 1}


class TestEmptySlice:
    def test_an_empty_span_yields_an_empty_document(self) -> None:
        doc = make_document(TEXT)
        part = doc.slice(Span(5, 5))
        assert part.text == ""
        assert len(part.tokens) == 0

    def test_an_empty_slice_still_carries_source_and_provenance(self) -> None:
        """US2 scenario 5 — provenance survives even when content does not."""
        doc = make_document(TEXT)
        part = doc.slice(Span(5, 5))
        assert part.source == doc.source
        assert part.provenance == doc.provenance


class TestIdentity:
    def test_a_slice_recomputes_nothing_it_should_not(self) -> None:
        """Source and provenance are unchanged, so the derived id matches.

        Identity is derived from blob and parser configuration (ADR-0002), none
        of which slicing changes. What distinguishes a slice is its origin.
        """
        doc = make_document(TEXT)
        assert doc.slice(Span(0, 7)).id == doc.id

    def test_origin_records_the_range_within_the_original(self) -> None:
        doc = make_document(TEXT)
        assert doc.slice(Span(12, 19)).origin == (Span(12, 19),)

    def test_nested_slices_compose_origins(self) -> None:
        doc = make_document(TEXT)
        assert doc.slice(Span(8, 20)).slice(Span(4, 11)).origin == (Span(12, 19),)


class TestErrors:
    def test_an_out_of_range_span_is_rejected(self) -> None:
        with pytest.raises(SpanError):
            make_document(TEXT).slice(Span(0, len(TEXT) + 1))

    def test_a_reversed_span_is_rejected(self) -> None:
        with pytest.raises(SpanError):
            make_document(TEXT).slice(Span(10, 2))


class TestImmutability:
    def test_the_original_is_untouched(self) -> None:
        doc = make_document(TEXT)
        before = (doc.text, len(doc.tokens), len(doc.pages), doc.origin)
        doc.slice(Span(0, 7))
        assert (doc.text, len(doc.tokens), len(doc.pages), doc.origin) == before
