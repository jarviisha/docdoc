"""T045 — Document.find (US4, FR-014).

Exact search is the primitive grounding will build on in Milestone 4. It is
deliberately the *only* search this layer provides (ADR-0005).
"""

from __future__ import annotations

import pytest

from docdoc.kernel import Span, SpanError
from tests.support import make_document


class TestMatching:
    def test_a_single_occurrence(self) -> None:
        doc = make_document("Invoice No: INV-001")
        assert doc.find("INV-001") == (Span(12, 19),)

    def test_multiple_occurrences_in_document_order(self) -> None:
        doc = make_document("abc xyz abc xyz abc")
        spans = doc.find("abc")
        assert spans == (Span(0, 3), Span(8, 11), Span(16, 19))
        assert [s.start for s in spans] == sorted(s.start for s in spans)

    def test_an_absent_string_returns_nothing(self) -> None:
        """A normal outcome, not an error (FR-014)."""
        assert make_document("Invoice No: INV-001").find("MISSING") == ()

    def test_matches_may_span_token_boundaries(self) -> None:
        doc = make_document("Invoice No: INV-001")
        assert doc.find("No: INV") == (Span(8, 15),)

    def test_matches_may_cover_whitespace_only(self) -> None:
        doc = make_document("a b")
        assert doc.find(" ") == (Span(1, 2),)


class TestOverlap:
    def test_overlapping_candidates_resolve_left_to_right(self) -> None:
        """ "aaa" occurs at 0, 1, and 2 in "aaaaa"; the documented rule resumes
        after each match, so only the first is reported.
        """
        doc = make_document("aaaaa")
        assert doc.find("aaa") == (Span(0, 3),)

    def test_non_overlapping_repeats_are_all_found(self) -> None:
        doc = make_document("aaaa")
        assert make_document("abab").find("ab") == (Span(0, 2), Span(2, 4))
        assert doc.find("aa") == (Span(0, 2), Span(2, 4))


class TestLiteralSemantics:
    def test_matching_is_case_sensitive(self) -> None:
        assert make_document("Invoice").find("invoice") == ()

    def test_no_unicode_normalization_is_applied(self) -> None:
        """Normalization belongs to the match view (ADR-0006), not here."""
        composed = "Conĝ"  # "Cô" as C + o + combining circumflex
        doc = make_document(composed)
        assert doc.find("Công") == ()
        assert doc.find(composed) == (Span(0, len(composed)),)

    def test_no_whitespace_collapsing_is_applied(self) -> None:
        assert make_document("a  b").find("a b") == ()


class TestUnicode:
    def test_positions_are_code_points(self) -> None:
        doc = make_document("Công ty ABC")
        assert doc.find("ty") == (Span(5, 7),)

    def test_characters_outside_the_bmp_count_as_one_position(self) -> None:
        doc = make_document("x 🧾 y")
        assert doc.find("🧾") == (Span(2, 3),)


class TestErrors:
    def test_an_empty_search_string_is_rejected(self) -> None:
        """Otherwise it would match at every position and mean nothing."""
        with pytest.raises(SpanError):
            make_document("Invoice").find("")


class TestDeterminism:
    def test_repeated_searches_return_identical_results(self) -> None:
        doc = make_document("abc abc abc")
        assert doc.find("abc") == doc.find("abc")

    def test_results_feed_locate_directly(self) -> None:
        """The exact path grounding will follow in Milestone 4."""
        doc = make_document("Invoice No: INV-001")
        (span,) = doc.find("INV-001")
        assert len(doc.locate(span)) == 1


class TestNoFuzzyParameter:
    def test_find_takes_only_the_search_string(self) -> None:
        """ADR-0005 — the kernel cannot host fuzzy matching without breaking its
        dependency rule, so the parameter does not exist rather than raising.
        """
        import inspect

        parameters = list(inspect.signature(make_document("a").find).parameters)
        assert parameters == ["text"]
