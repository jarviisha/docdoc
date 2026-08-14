"""T035 — Document.merge (US2, FR-011, FR-013)."""

from __future__ import annotations

from itertools import pairwise

import pytest

from docdoc.kernel import Document, MergeError, Span
from tests.support import make_document

TEXT = "Invoice No: INV-001\nTotal: 125000"


def partition(doc: Document, *cuts: int) -> tuple[Document, ...]:
    bounds = [0, *cuts, len(doc.text)]
    return tuple(doc.slice(Span(start, end)) for start, end in pairwise(bounds))


class TestBasicMerge:
    def test_merging_a_partition_reproduces_the_text(self) -> None:
        doc = make_document(TEXT)
        assert Document.merge(partition(doc, 12, 20)).text == TEXT

    def test_merging_a_single_part_reproduces_it(self) -> None:
        doc = make_document(TEXT)
        part = doc.slice(Span(0, 19))
        merged = Document.merge((part,))
        assert merged.text == part.text
        assert tuple(merged.tokens) == tuple(part.tokens)

    def test_token_spans_are_rebased_onto_the_merged_text(self) -> None:
        doc = make_document(TEXT)
        merged = Document.merge(partition(doc, 20))
        for token in merged.tokens:
            assert (
                merged.text[token.span.start : token.span.end]
                == TEXT[token.span.start : token.span.end]
            )

    def test_geometry_is_carried_through_unchanged(self) -> None:
        doc = make_document(TEXT, page_breaks=(20,))
        merged = Document.merge(partition(doc, 20))
        original = {t.span: t.geometry for t in doc.tokens}
        for token in merged.tokens:
            assert token.geometry == original[token.span]


class TestPages:
    def test_pages_are_coalesced_by_index(self) -> None:
        doc = make_document(TEXT, page_breaks=(20,))
        merged = Document.merge(partition(doc, 12, 26))
        assert [p.index for p in merged.pages] == [0, 1]

    def test_merged_page_spans_tile_the_text(self) -> None:
        doc = make_document(TEXT, page_breaks=(20,))
        merged = Document.merge(partition(doc, 12, 26))
        cursor = 0
        for page in merged.pages:
            assert page.span.start == cursor
            cursor = page.span.end
        assert cursor == len(merged.text)


class TestNonAdjacentParts:
    def test_non_adjacent_parts_may_be_merged(self) -> None:
        """Permitted so windowed extraction stays possible (research.md R6)."""
        doc = make_document(TEXT)
        merged = Document.merge((doc.slice(Span(0, 7)), doc.slice(Span(12, 19))))
        assert merged.text == "InvoiceINV-001"

    def test_geometry_still_points_at_the_true_original_location(self) -> None:
        """The merged text never existed contiguously, but provenance holds."""
        doc = make_document(TEXT)
        original = {t.span: t.geometry for t in doc.tokens}
        merged = Document.merge((doc.slice(Span(0, 7)), doc.slice(Span(12, 19))))
        geometries = [t.geometry for t in merged.tokens]
        assert geometries == [original[Span(0, 7)], original[Span(12, 19)]]

    def test_origin_records_both_disjoint_ranges(self) -> None:
        doc = make_document(TEXT)
        merged = Document.merge((doc.slice(Span(0, 7)), doc.slice(Span(12, 19))))
        assert merged.origin == (Span(0, 7), Span(12, 19))


class TestErrors:
    def test_merging_zero_parts_is_an_error(self) -> None:
        with pytest.raises(MergeError) as excinfo:
            Document.merge(())
        assert excinfo.value.reason == "no_parts"

    def test_parts_from_different_files_are_rejected(self) -> None:
        a = make_document(TEXT, data=b"file-a")
        b = make_document(TEXT, data=b"file-b")
        with pytest.raises(MergeError) as excinfo:
            Document.merge((a.slice(Span(0, 7)), b.slice(Span(8, 11))))
        assert excinfo.value.reason == "mismatched_source"

    def test_parts_from_different_parser_versions_are_rejected(self) -> None:
        a = make_document(TEXT, parser_version="1.0.0")
        b = make_document(TEXT, parser_version="2.0.0")
        with pytest.raises(MergeError) as excinfo:
            Document.merge((a.slice(Span(0, 7)), b.slice(Span(8, 11))))
        assert excinfo.value.reason == "mismatched_source"

    def test_overlapping_parts_are_rejected(self) -> None:
        """Overlap would duplicate tokens and break the non-overlap invariant
        that the span index depends on.
        """
        doc = make_document(TEXT)
        with pytest.raises(MergeError) as excinfo:
            Document.merge((doc.slice(Span(0, 12)), doc.slice(Span(8, 19))))
        assert excinfo.value.reason == "overlapping_parts"

    def test_parts_out_of_order_are_rejected(self) -> None:
        doc = make_document(TEXT)
        with pytest.raises(MergeError) as excinfo:
            Document.merge((doc.slice(Span(12, 19)), doc.slice(Span(0, 7))))
        assert excinfo.value.reason in {"unordered_parts", "overlapping_parts"}

    def test_the_error_names_the_parts_involved(self) -> None:
        a = make_document(TEXT, data=b"file-a")
        b = make_document(TEXT, data=b"file-b")
        with pytest.raises(MergeError) as excinfo:
            Document.merge((a, b))
        assert len(excinfo.value.part_ids) == 2
