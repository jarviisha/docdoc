"""Carrying a position in the folded text back to a position in the source.

ADR-0006 calls this "the highest-risk component in the grounding path", and the
reason is worth stating where the code is: **an incorrect map does not crash and
does not return nothing. It returns a grounded-looking value pointing at the
wrong place.** Every other failure in this feature is loud. This one is not, so
it carries the strongest tests outside the kernel.

Why the map is explicit rather than arithmetic: the transformations change length
in *both* directions. Measured on the committed fixtures -- a ligature expands
``fi`` from one character to two, while a combining mark composes ``e`` + U+0301
into one, shrinking the text by four characters over one fixture. A single
running delta cannot describe that.

Why a segment list rather than one source offset per view character: the
transformations are identity over long runs, so a page of ordinary text is one
segment. A dense array costs a Python integer per character to store what a
handful of segments already say, and the lookup it buys runs once per resolved
value, not once per character.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import NamedTuple

from docdoc.grounding.errors import GroundingError
from docdoc.kernel import Span

__all__ = ["OffsetMap", "Segment"]


class Segment(NamedTuple):
    """A run over which view-to-source mapping is a constant offset.

    ``length`` counts *view* characters. A segment of length 0 records a source
    region the view deleted -- a soft hyphen, a joined line break -- which has no
    view position of its own but must still be skipped over when mapping back.
    """

    view_start: int
    source_start: int
    length: int
    #: How many source characters this segment consumed. Equal to ``length`` for
    #: an untouched run; larger where the view collapsed or deleted something;
    #: smaller where NFKC expanded one source character into several.
    source_length: int

    #: Whether the view copied this run through **character for character**, so a
    #: position inside it maps to source by simple addition.
    #:
    #: This is a flag rather than the ``length == source_length`` test an earlier
    #: version inferred it from, because that test is wrong and Hypothesis found
    #: the case: a ligature carrying a combining mark -- ``ﬁ`` + U+0301 -- folds to
    #: ``fí``, two view characters from two source characters. Equal lengths, and
    #: emphatically *not* a character-wise mapping: view position 1 there is the
    #: ``í`` that the *first* source character contributed the ``i`` of. Mapping it
    #: by addition landed on the combining mark alone and returned a range that had
    #: lost half the match -- the narrowing this whole module exists to prevent.
    identity: bool = False


class OffsetMap:
    """View positions to source positions: total, monotonic, and outward.

    Not a ``pydantic`` model. It is built once per grounding run over text that
    can be tens of thousands of characters, it is never serialised, and FR-013
    forbids exposing it to consumers at all -- so validation on construction
    would cost measurably and buy nothing.
    """

    __slots__ = ("_segments", "_source_len", "_starts", "_tail_start", "_view_len")

    def __init__(self, segments: tuple[Segment, ...], view_len: int, source_len: int) -> None:
        self._segments = segments
        self._starts = tuple(s.view_start for s in segments)
        self._view_len = view_len
        self._source_len = source_len
        self._validate()
        # Where a range *starting* at the very end of the view begins. Source the
        # view deleted at the tail -- a trailing soft hyphen, and in the
        # degenerate case a document that is nothing else -- has no view position
        # of its own, so mapping outward has to reach back over it. Precomputed
        # because it is a property of the map, not of the query.
        tail = source_len
        for seg in reversed(segments):
            if seg.length != 0:
                break
            tail = seg.source_start
        self._tail_start = tail

    def _validate(self) -> None:
        """Fail loudly rather than return a plausible wrong range (GRD-7, GRD-8).

        A defensive check, not an expected condition. It runs once per run over a
        list whose length is the number of *transformations*, not the number of
        characters, so it is cheap even on a large document.
        """
        expected_view = 0
        expected_source = 0
        for seg in self._segments:
            if seg.view_start != expected_view:
                raise GroundingError(
                    "offset map is not contiguous in view space: "
                    f"segment starts at {seg.view_start}, expected {expected_view}"
                )
            if seg.source_start != expected_source:
                raise GroundingError(
                    "offset map is not contiguous in source space: "
                    f"segment starts at {seg.source_start}, expected {expected_source}"
                )
            if seg.length < 0 or seg.source_length < 0:
                raise GroundingError("offset map segment has negative length")
            expected_view += seg.length
            expected_source += seg.source_length
        if expected_view != self._view_len:
            raise GroundingError(
                f"offset map covers {expected_view} view characters, view has {self._view_len}"
            )
        if expected_source != self._source_len:
            raise GroundingError(
                f"offset map covers {expected_source} source characters, "
                f"source has {self._source_len}"
            )

    @property
    def segments(self) -> tuple[Segment, ...]:
        return self._segments

    def __len__(self) -> int:
        return len(self._segments)

    def __repr__(self) -> str:
        return f"OffsetMap({len(self._segments)} segments, view={self._view_len})"

    # ------------------------------------------------------------------
    # Mapping
    # ------------------------------------------------------------------

    def source_start_for(self, view_pos: int) -> int:
        """The source position a range *starting* at ``view_pos`` should start at.

        Maps outward: where ``view_pos`` falls at the boundary of a region the
        view deleted or collapsed, this returns the **start** of that source
        region, so the resulting range widens to contain it rather than clipping
        into it (GRD-9, research.md R10).
        """
        return self._map(view_pos, at_end=False)

    def source_end_for(self, view_pos: int) -> int:
        """The source position a range *ending* at ``view_pos`` should end at.

        The mirror of :meth:`source_start_for`: maps outward to the **end** of a
        containing source region, so a range widens rather than narrows.
        """
        return self._map(view_pos, at_end=True)

    def source_span_for(self, view_start: int, view_end: int) -> Span:
        """The smallest source range containing the given view range.

        This is the only mapping the rest of the layer uses, and the asymmetry in
        it is the whole safety property: a round trip may **widen** a range, and
        must never narrow or move one. Widening yields a slightly larger set of
        token boxes, which is coarse. Narrowing yields a box that omits part of
        the value, which is wrong.
        """
        if not 0 <= view_start <= view_end <= self._view_len:
            raise GroundingError(
                f"view range [{view_start}, {view_end}) does not fit a view of "
                f"length {self._view_len}"
            )
        return Span(self.source_start_for(view_start), self.source_end_for(view_end))

    def _map(self, view_pos: int, *, at_end: bool) -> int:
        if not 0 <= view_pos <= self._view_len:
            raise GroundingError(
                f"view position {view_pos} does not fit a view of length {self._view_len}"
            )
        if view_pos == self._view_len:
            # A range ending here ends at the end of the source. A range starting
            # here starts before any source the view deleted at the tail, so that
            # it still contains it -- outward, as everywhere else.
            return self._source_len if at_end else self._tail_start

        index = bisect_right(self._starts, view_pos) - 1
        # Zero-length segments share a `view_start` with the segment that follows
        # them. `bisect_right` lands after the run of them, which is what a range
        # *ending* here wants: the deleted source is behind it, so including it
        # widens. A range *starting* here wants the earliest of the run, so the
        # deleted source is ahead of it and including it widens too.
        if not at_end:
            while (
                index > 0
                and self._segments[index - 1].length == 0
                and self._segments[index - 1].view_start == view_pos
            ):
                index -= 1
        seg = self._segments[index]
        offset = view_pos - seg.view_start

        if seg.identity:
            # Copied through character for character, so addition is exact.
            return seg.source_start + offset

        # A segment the transformations rewrote. Positions *inside* it have no
        # source position of their own, and pretending otherwise is how a range
        # gets narrowed:
        #
        #   collapsed ('   ' -> ' '): the view has fewer characters here than the
        #     source did, so an interior offset would fall short.
        #   expanded  ('ﬁ' -> 'fi'): the view has more, so an interior offset
        #     would walk past the single source character that produced them.
        #   deleted   (soft hyphen): zero view characters over one source.
        #
        # A position at the segment's *boundary* is a different case, and
        # conflating the two over-widens rather than under-widens. `offset == 0`
        # means the position sits between this segment and the one before it, so
        # both directions map to `source_start`: a range ending there stops before
        # the rewritten region, and one starting there starts at its near edge.
        # Returning the far edge for an end here appended whatever the segment
        # covered -- a claim ending at a line break came back with the newline
        # attached, which is widening past what was matched rather than widening
        # to contain it.
        if at_end and offset > 0:
            return seg.source_start + seg.source_length
        return seg.source_start
