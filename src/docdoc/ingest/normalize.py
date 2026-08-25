"""Turning a parser's raw output into something the kernel will accept.

Two jobs, both belonging to the boundary rather than to any one adapter:

**Coordinates.** Every provider has its own units and origin; the kernel has
one. Conversion happens here, in the adapter's own layer, so no native
coordinate system ever reaches a Document (FR-005).

**Text.** Canonical text is assembled *from the tokens*, so the correspondence
between a token and its text is exact by construction rather than recovered by
searching (research.md R6). No normalization is applied on top: whitespace runs
survive, hyphenated line breaks are not rejoined, and no table is linearized
(FR-007).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from docdoc.kernel import (
    BBox,
    BlobRef,
    Document,
    Geometry,
    GeometryError,
    IngestProvenance,
    Page,
    Span,
    Token,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["DocumentBuilder", "PageFrame", "Word", "normalize_bbox"]

#: How far outside the page a box may sit before it stops being rendering slop
#: and starts being a bug, as a fraction of the page dimension.
#:
#: Glyph boxes a hair outside the MediaBox are ordinary, and failing a whole
#: document over a fraction of a point would be useless. A box 30% off the page
#: is not slop -- it is a coordinate-system or rotation error, and that is
#: exactly the class of mistake this project must not absorb silently
#: (research.md R7).
OUT_OF_PAGE_TOLERANCE = 0.01


def normalize_bbox(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    *,
    width: float,
    height: float,
    page_index: int,
    tolerance: float = OUT_OF_PAGE_TOLERANCE,
) -> BBox:
    """Convert one box from page units to normalized 0..1, top-left origin.

    Raises:
        GeometryError: the page has no area, or a coordinate is further outside
            the page than ``tolerance`` allows.
    """
    if width <= 0 or height <= 0:
        raise GeometryError(
            f"page {page_index} has no area ({width} x {height}); cannot normalize geometry",
            page_index=page_index,
        )

    # Some producers emit a box with its corners the wrong way round. Ordering
    # them is not a repair of *content*, only of notation -- the rectangle is
    # unchanged -- so it is safe here in a way that reordering tokens is not.
    left, right = sorted((x0, x1))
    top, bottom = sorted((y0, y1))

    scaled = (left / width, top / height, right / width, bottom / height)
    clamped = tuple(
        _clamp_or_raise(value, tolerance=tolerance, page_index=page_index, raw=(x0, y0, x1, y1))
        for value in scaled
    )
    return BBox.create(*clamped)


#: Slack on the tolerance comparison itself.
#:
#: The tolerance is an engineering threshold, not an exact quantity, and dividing
#: a coordinate by a page dimension rounds. A box exactly ``tolerance`` outside
#: the page can normalize to a value a single bit beyond it -- found by the
#: property suite at width 1.0, height 1733.75, where the y coordinate came out
#: -0.010000000000000002. Rejecting a document over that last bit would be
#: indefensible, so the comparison carries slack while the threshold itself stays
#: exactly 1%.
_COMPARISON_SLACK = 1e-9


def _clamp_or_raise(
    value: float,
    *,
    tolerance: float,
    page_index: int,
    raw: tuple[float, float, float, float],
) -> float:
    limit = tolerance + _COMPARISON_SLACK
    if -limit <= value < 0.0:
        return 0.0
    if 1.0 < value <= 1.0 + limit:
        return 1.0
    if 0.0 <= value <= 1.0:
        return value
    raise GeometryError(
        f"normalized coordinate {value:.4f} on page {page_index} is more than "
        f"{tolerance:.0%} outside the page; this is a coordinate-system error, not "
        "rendering slop, and a wrong box is worse than a missing one",
        bbox=raw,
        page_index=page_index,
    )


#: One word as an adapter hands it over: its text, its box, and the provider's
#: own confidence if it reported one. A plain 2-tuple is still accepted, because
#: an offline parser has no confidence to report and should not have to say so.
Word = tuple[str, "BBox | None"] | tuple[str, "BBox | None", float | None]


@dataclass(slots=True)
class PageFrame:
    """One page under construction."""

    index: int
    width: float
    height: float
    rotation: int
    start: int
    lines: list[list[tuple[str, BBox | None, float | None]]] = field(default_factory=list)


class DocumentBuilder:
    """Accumulates a parser's words and produces a valid Document.

    The builder owns the invariants the kernel will check anyway -- page spans
    tiling the text, tokens ascending and non-overlapping -- so an adapter
    cannot get them subtly wrong. What it does *not* do is reorder anything: the
    order words are added in is the order they appear in, because reading order
    is the adapter's declared property (FR-037).
    """

    __slots__ = ("_geometry", "_pages")

    def __init__(self, *, geometry: bool) -> None:
        #: All-or-nothing, mirroring the kernel's DOC-8. A parser with partial
        #: geometry must declare it has none.
        self._geometry = geometry
        self._pages: list[PageFrame] = []

    def start_page(self, *, width: float, height: float, rotation: int = 0) -> None:
        self._pages.append(
            PageFrame(
                index=len(self._pages),
                width=width,
                height=height,
                rotation=rotation,
                start=0,
            )
        )

    def add_line(self, words: Sequence[Word]) -> None:
        """Add one line of words to the page opened most recently.

        A word is ``(text, bbox)`` or ``(text, bbox, confidence)``. Confidence is
        the provider's own number, carried through untouched and never
        interpreted here (ADR-0004).
        """
        if not self._pages:
            raise ValueError("start_page() must be called before add_line()")
        kept = [(word[0], word[1], word[2] if len(word) > 2 else None) for word in words if word[0]]
        if kept:
            self._pages[-1].lines.append(kept)

    @property
    def page_count(self) -> int:
        return len(self._pages)

    def build(self, *, source: BlobRef, provenance: IngestProvenance) -> Document:
        """Assemble the text, place every token in it, and construct.

        Identity is derived by ``Document.create`` from the source and
        provenance, not passed in: an adapter must not be able to hand over an
        id that does not match what produced the document (DOC-9).
        """
        chunks: list[str] = []
        pages: list[Page] = []
        tokens: list[Token] = []
        cursor = 0

        for frame in self._pages:
            page_start = cursor
            for line in frame.lines:
                for position, (word, box, confidence) in enumerate(line):
                    if position:
                        chunks.append(" ")
                        cursor += 1
                    start = cursor
                    chunks.append(word)
                    cursor += len(word)
                    tokens.append(
                        Token(
                            span=Span(start, cursor),
                            geometry=(
                                Geometry(page_index=frame.index, bbox=box)
                                if self._geometry and box is not None
                                else None
                            ),
                            source_confidence=confidence,
                        )
                    )
                # Line break. Part of the page's own span, so pages still tile.
                chunks.append("\n")
                cursor += 1

            pages.append(
                Page(
                    index=frame.index,
                    span=Span(page_start, cursor),
                    width=frame.width,
                    height=frame.height,
                    rotation=frame.rotation,
                )
            )

        return Document.create(
            text="".join(chunks),
            pages=pages,
            tokens=tokens,
            provenance=provenance,
            source=source,
        )
