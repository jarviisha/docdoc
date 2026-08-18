"""What one grounding says, and what it refuses to let a caller confuse.

Three shapes here exist to keep facts apart that a looser model would collapse:

* ``geometry is None`` (unavailable) versus ``geometry == ()`` (nothing there).
* A value the model reported absent, which gets **no outcome at all**, versus a
  value asserted with no evidence, which gets ``ungrounded``.
* An exact score, assigned structurally, versus a fuzzy score, measured. They are
  not comparable, and nothing here ranks across them.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from docdoc.grounding.options import GroundingOptions
from docdoc.kernel import Geometry, Span

__all__ = [
    "Alternative",
    "GroundingCounts",
    "GroundingOutcome",
    "GroundingProvenance",
    "GroundingResult",
    "GroundingStatus",
]


class GroundingStatus(StrEnum):
    """The closed vocabulary of outcomes (GRD-1).

    Three members, and the closure is constitutional rather than stylistic:
    Principle II fixes exact -> fuzzy -> ungrounded as the order grounding is
    computed in, and ADR-0005 says adding a fourth state "is an amendment, not an
    implementation detail". Ambiguity is expressed through ``alternatives``.

    **``EXACT`` means verbatim modulo documented, versioned cosmetic folding** --
    *not* byte-identical in the raw source. After ADR-0006 a value whose source
    spells "Invoice" with an fi ligature resolves as ``EXACT``, and the range
    returned points at the ligature. A reader who assumes the stronger meaning
    would be misled, which is why this says so here rather than only in a design
    document: a docstring travels where a design document does not.
    """

    EXACT = "exact"
    FUZZY = "fuzzy"
    UNGROUNDED = "ungrounded"


class Alternative(BaseModel):
    """A runner-up range, retained so ambiguity is visible rather than resolved away."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A source-text range, like every range this layer returns.
    span: Span
    score: float

    # Pages and boxes are deliberately absent. Resolve them on demand with
    # `document.page_for(alt.span)` and `document.locate(alt.span)`. Most
    # alternatives are never inspected -- they matter when a human is disputing
    # one value -- and resolving geometry for five per value, for every value,
    # would multiply the kernel geometry work sixfold to serve that case.


class GroundingOutcome(BaseModel):
    """One value's result."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    field_path: str
    status: GroundingStatus

    #: ``1.0`` for exact, the measured similarity for fuzzy, ``None`` for
    #: ungrounded (GRD-14).
    score: float | None = Field(
        default=None,
        description=(
            "Trusted and docdoc-computed. NOT COMPARABLE ACROSS TIERS: an exact "
            "score is 1.0 by definition while a fuzzy score is a measured "
            "similarity, so ranking values against each other by this number is "
            "meaningless and nothing in docdoc does it (ADR-0004)."
        ),
    )

    #: A range into ``Document.text``. ``None`` if and only if ungrounded.
    span: Span | None = None

    pages: tuple[int, ...] = ()

    #: ``None`` means the parser supplied no geometry -- unavailable. An empty
    #: tuple means geometry exists and this range covers no tokens. Collapsing
    #: the two would let a caller read "unavailable" as "nothing is there"
    #: (GRD-17, FR-006).
    geometry: tuple[Geometry, ...] | None = None

    alternatives: tuple[Alternative, ...] = ()

    #: The candidate budget was reached for this value. It is still resolved from
    #: the candidates examined; this is what stops the cap being silent.
    truncated: bool = False

    @property
    def grounded(self) -> bool:
        return self.status is not GroundingStatus.UNGROUNDED


class GroundingCounts(BaseModel):
    """Per-outcome totals, so the grounding rate needs no second pass (FR-035)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    exact: int = 0
    fuzzy: int = 0
    ungrounded: int = 0

    #: Values the model reported absent. Carried **separately and outside the
    #: rate** so the denominator convention lives in the data rather than being
    #: reinvented by every consumer. A correctly reported absence is not a
    #: grounding failure (FR-008).
    not_applicable: int = 0

    truncated: int = 0

    @property
    def grounding_rate(self) -> float | None:
        """``(exact + fuzzy) / (exact + fuzzy + ungrounded)``, or ``None`` if empty.

        ``None`` rather than ``0.0`` for a result with nothing to ground: a rate
        of zero would read as total failure, and there was no question asked.
        """
        denominator = self.exact + self.fuzzy + self.ungrounded
        if denominator == 0:
            return None
        return (self.exact + self.fuzzy) / denominator


class GroundingProvenance(BaseModel):
    """Everything needed to explain one grounding after all three versions moved."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    extraction_artifact_id: str
    grounding_version: str
    match_view_version: str
    options: GroundingOptions
    grounder_id: str
    grounder_version: str


class GroundingResult(BaseModel):
    """One outcome per value that had a claim, plus counts, provenance, and identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Keyed by field path. A value the model reported absent has **no entry** --
    #: see GroundingCounts.not_applicable.
    outcomes: dict[str, GroundingOutcome]
    counts: GroundingCounts
    provenance: GroundingProvenance
    artifact_id: str
