"""T033 — the invariants the whole product rests on (FR-012, SC-002).

Constitution Quality Gate 2 blocks all higher-layer work until these pass. If
``slice``/``merge`` can silently lose a mapping, every grounded value docdoc
ever produces is suspect -- and the failure would surface as a wrong bounding
box in production rather than as a test failure here.
"""

from __future__ import annotations

from itertools import pairwise

from hypothesis import given
from hypothesis import strategies as st

from docdoc.kernel import Document, Span
from tests.property.strategies import documents, documents_with_partition, spans_within


class TestRoundTrip:
    """The foundational invariant: cutting and reassembling changes nothing."""

    @given(documents_with_partition(with_geometry=True), st.data())
    def test_locate_is_unchanged_by_a_partition_round_trip(
        self, pair: tuple[Document, tuple[Document, ...]], data: st.DataObject
    ) -> None:
        """``locate(s) == merge(partition(d)).locate(s)`` -- FR-012, SC-002.

        Geometry is required rather than skipped: skipping inside ``@given``
        would abandon the whole test on the first geometry-less example, which
        would quietly disable the most important assertion in the suite.
        """
        document, parts = pair
        merged = Document.merge(parts)
        span = data.draw(spans_within(document))
        assert document.locate(span) == merged.locate(span)

    @given(documents_with_partition())
    def test_a_partition_round_trip_reproduces_the_text(
        self, pair: tuple[Document, tuple[Document, ...]]
    ) -> None:
        document, parts = pair
        assert Document.merge(parts).text == document.text

    @given(documents_with_partition())
    def test_a_partition_round_trip_preserves_every_token(
        self, pair: tuple[Document, tuple[Document, ...]]
    ) -> None:
        document, parts = pair
        merged = Document.merge(parts)
        assert tuple(merged.tokens) == tuple(document.tokens)

    @given(documents_with_partition())
    def test_a_partition_round_trip_preserves_page_numbers(
        self, pair: tuple[Document, tuple[Document, ...]]
    ) -> None:
        document, parts = pair
        merged = Document.merge(parts)
        original_pages = [p.index for p in document.pages if not p.span.is_empty]
        merged_pages = [p.index for p in merged.pages if not p.span.is_empty]
        assert merged_pages == original_pages

    @given(documents_with_partition(), st.data())
    def test_page_resolution_is_unchanged_by_a_round_trip(
        self, pair: tuple[Document, tuple[Document, ...]], data: st.DataObject
    ) -> None:
        """Holds regardless of whether geometry is available."""
        document, parts = pair
        merged = Document.merge(parts)
        span = data.draw(spans_within(document))
        assert document.page_for(span) == merged.page_for(span)


class TestSlice:
    @given(documents(), st.data())
    def test_sliced_text_equals_the_original_range(
        self, document: Document, data: st.DataObject
    ) -> None:
        span = data.draw(spans_within(document))
        assert document.slice(span).text == document.text[span.start : span.end]

    @given(documents(), st.data())
    def test_retained_token_geometry_is_bit_identical(
        self, document: Document, data: st.DataObject
    ) -> None:
        """Geometry is page-absolute and must survive text rebasing untouched."""
        span = data.draw(spans_within(document))
        part = document.slice(span)
        original = {t.span: t.geometry for t in document.tokens}
        for token in part.tokens:
            assert token.geometry == original[token.span.shift(span.start)]

    @given(documents(), st.data())
    def test_retained_tokens_still_cover_their_own_text(
        self, document: Document, data: st.DataObject
    ) -> None:
        span = data.draw(spans_within(document))
        part = document.slice(span)
        for token in part.tokens:
            original_span = token.span.shift(span.start)
            assert (
                part.text[token.span.start : token.span.end]
                == document.text[original_span.start : original_span.end]
            )

    @given(documents(), st.data())
    def test_slicing_everything_is_an_identity_on_text_and_tokens(
        self, document: Document, data: st.DataObject
    ) -> None:
        whole = document.slice(Span(0, len(document.text)))
        assert whole.text == document.text
        assert tuple(whole.tokens) == tuple(document.tokens)

    @given(documents(), st.data())
    def test_origin_tracks_the_original_range(
        self, document: Document, data: st.DataObject
    ) -> None:
        span = data.draw(spans_within(document))
        part = document.slice(span)
        covered = sum(len(piece) for piece in part.origin)
        assert covered == len(part.text)


class TestSpanIndexAgreement:
    @given(documents(), st.data())
    def test_index_agrees_with_a_linear_scan(self, document: Document, data: st.DataObject) -> None:
        """The binary search must never disagree with the obvious implementation."""
        span = data.draw(spans_within(document))
        expected = tuple(t for t in document.tokens if t.span.intersects(span))
        assert document.tokens.tokens_in(span) == expected

    @given(documents(), st.data())
    def test_token_at_agrees_with_a_linear_scan(
        self, document: Document, data: st.DataObject
    ) -> None:
        position = data.draw(st.integers(min_value=0, max_value=max(len(document.text), 1)))
        expected = next((t for t in document.tokens if t.span.contains(position)), None)
        assert document.tokens.token_at(position) == expected


class TestIdentityDeterminism:
    @given(documents())
    def test_identity_is_stable_across_repeated_derivation(self, document: Document) -> None:
        rebuilt = Document.create(
            text=document.text,
            pages=document.pages,
            tokens=tuple(document.tokens),
            blocks=document.blocks,
            tables=document.tables,
            provenance=document.provenance,
            source=document.source,
            origin=document.origin,
        )
        assert rebuilt.id == document.id

    @given(documents())
    def test_slicing_preserves_identity_inputs(self, document: Document) -> None:
        """Identity derives from blob and parser config, which slicing never changes."""
        part = document.slice(Span(0, len(document.text)))
        assert part.id == document.id


class TestFind:
    @given(documents(), st.data())
    def test_every_match_is_a_real_occurrence(
        self, document: Document, data: st.DataObject
    ) -> None:
        if not document.text:
            return
        span = data.draw(spans_within(document))
        needle = document.text[span.start : span.end]
        if not needle:
            return
        for match in document.find(needle):
            assert document.text[match.start : match.end] == needle

    @given(documents(), st.data())
    def test_matches_never_overlap(self, document: Document, data: st.DataObject) -> None:
        if not document.text:
            return
        span = data.draw(spans_within(document))
        needle = document.text[span.start : span.end]
        if not needle:
            return
        matches = document.find(needle)
        for earlier, later in pairwise(matches):
            assert earlier.end <= later.start


class TestLocate:
    @given(documents(with_geometry=True), st.data())
    def test_every_returned_box_belongs_to_an_intersecting_token(
        self, document: Document, data: st.DataObject
    ) -> None:
        span = data.draw(spans_within(document))
        expected = tuple(
            t.geometry for t in document.tokens if t.span.intersects(span) and t.geometry
        )
        assert document.locate(span) == expected

    @given(documents(with_geometry=True), st.data())
    def test_an_empty_span_never_resolves(self, document: Document, data: st.DataObject) -> None:
        position = data.draw(st.integers(min_value=0, max_value=len(document.text)))
        assert document.locate(Span(position, position)) == ()

    @given(documents(with_geometry=True), st.data())
    def test_every_box_lies_within_the_unit_square(
        self, document: Document, data: st.DataObject
    ) -> None:
        span = data.draw(spans_within(document))
        for geometry in document.locate(span):
            box = geometry.bbox
            assert 0.0 <= box.x0 <= box.x1 <= 1.0
            assert 0.0 <= box.y0 <= box.y1 <= 1.0
