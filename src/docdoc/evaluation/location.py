"""`page_box@1` — whether a value was grounded where the label says it is.

A separate axis from the field outcome of EVA-11, and it must stay separate: a
value can be correct and mislocated, or wrong and perfectly located, and a report
that collapsed the two would hide the failure this rule exists to catch -- a
plausible wrong span that resolves cleanly and points at the wrong text.

The rule:

1. The expected page must appear in the outcome's recorded ``pages``. Necessary
   in every case.
2. Where the label states a box **and** the outcome carries geometry, the recorded
   area on that page lying inside the expected box must be at least
   :data:`CONTAINMENT_THRESHOLD` of the recorded area.

**Containment, not IoU.** A human labelling a value draws a loose box around it;
docdoc's geometry is tight on the tokens. IoU punishes exactly that pairing -- a
perfectly located tight box inside a generous hand-drawn one can score well below
0.5 IoU while being completely right. Containment asks the question actually being
tested: is what docdoc found inside what the human pointed at? If labels ever
become machine-tight rather than hand-drawn, IoU becomes the better measure and
gets a ``page_box@2``.

**The threshold is fixed by the version and is not an option** (EVA-22a). A
caller-settable threshold would let two reports both claiming ``page_box@1``
disagree about the mislocation rate, and the comparison refusal does not read it,
so ``compare()`` would diff them silently.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docdoc.evaluation.labels import ExpectedLocation
    from docdoc.grounding.result import GroundingOutcome
    from docdoc.kernel import BBox

__all__ = [
    "CONTAINMENT_THRESHOLD",
    "LOCATION_RULE_VERSION",
    "LocationAgreement",
    "agreement_for",
]

LOCATION_RULE_VERSION = "page_box@1"

#: The fraction of the recorded area that must lie inside the expected box.
CONTAINMENT_THRESHOLD = 0.5


class LocationAgreement(StrEnum):
    """Closed, three-valued, and never a seventh field outcome (EVA-14)."""

    AGREES = "agrees"
    DISAGREES = "disagrees"

    #: The label states a box and the parser supplied no geometry.
    #:
    #: **Never reported as DISAGREES.** ``GroundingOutcome.geometry is None``
    #: means the parser supplied none; ``()`` means geometry exists and covers no
    #: tokens. Milestone 4's GRD-17 refuses to collapse those two, and collapsing
    #: the first into a disagreement here would report a parser's silence as a
    #: grounding error -- making the mislocation rate a function of which parser
    #: ran rather than of where values were found.
    NOT_ASSESSABLE = "not_assessable"


def _intersection_area(a: BBox, b: BBox) -> float:
    width = min(a.x1, b.x1) - max(a.x0, b.x0)
    height = min(a.y1, b.y1) - max(a.y0, b.y0)
    if width <= 0 or height <= 0:
        return 0.0
    return width * height


def _area(box: BBox) -> float:
    return max(0.0, box.x1 - box.x0) * max(0.0, box.y1 - box.y0)


def agreement_for(
    expected: ExpectedLocation | None,
    outcome: GroundingOutcome | None,
) -> LocationAgreement | None:
    """Compare a recorded location against a label's expected one.

    Returns ``None`` when the label states no location at all -- there is nothing
    to agree or disagree with, and FR-018 says only the grounding rate is
    measurable for such a field.
    """
    if expected is None:
        return None
    if outcome is None or not outcome.grounded:
        # Ungrounded is not mislocated. It is already counted by the grounding
        # rate, and counting it again here would charge one failure twice.
        return LocationAgreement.NOT_ASSESSABLE

    if expected.page not in outcome.pages:
        return LocationAgreement.DISAGREES

    if expected.bbox is None:
        return LocationAgreement.AGREES

    if outcome.geometry is None:
        return LocationAgreement.NOT_ASSESSABLE

    on_page = [g for g in outcome.geometry if g.page_index == expected.page]
    if not on_page:
        # Geometry exists but covers nothing on the expected page. That is a
        # real disagreement, not an absence of evidence: the parser did supply
        # geometry, and none of it is where the label says.
        return LocationAgreement.DISAGREES

    recorded = sum(_area(g.bbox) for g in on_page)
    if recorded <= 0:
        return LocationAgreement.NOT_ASSESSABLE

    inside = sum(_intersection_area(g.bbox, expected.bbox) for g in on_page)
    ratio = inside / recorded
    return (
        LocationAgreement.AGREES if ratio >= CONTAINMENT_THRESHOLD else LocationAgreement.DISAGREES
    )
