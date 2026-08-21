"""The closed set of field outcomes, and the record of one comparison.

Six members, and no reported shape may collapse two of them (FR-025). Each pair
that a looser design would have merged is a pair with different fixes:

- **missing** and **incorrect** — a blank and a wrong answer are different
  failures. One says the model found nothing; the other says it found the wrong
  thing. Merging them makes "why is accuracy down?" unanswerable.
- **spurious** and **incorrect** — inventing a value for a field that should be
  empty is not the same as misreading one that should not.
- **unlabeled** and **correct** — a prediction the golden set says nothing about
  is neither right nor wrong, and treating it as either makes accuracy a function
  of how completely the dataset happens to be labelled.
- **unevaluated** and everything else — a document with no prediction is not a
  document that failed. Both stay in the denominator; only one is a defect.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict

# Runtime imports, not TYPE_CHECKING: pydantic resolves field annotations when it
# builds the model. `docdoc.grounding.result` is the submodule, never the
# `docdoc.grounding` package -- see the note in `predictions.py`.
from docdoc.evaluation.location import LocationAgreement
from docdoc.evaluation.tiers import Tier
from docdoc.grounding.result import GroundingStatus

__all__ = ["FieldOutcome", "FieldOutcomeKind"]


class FieldOutcomeKind(StrEnum):
    """Closed (EVA-11). Six members; a seventh is a specification change."""

    #: Label and prediction agree — a matching value, or a correctly reported
    #: absence. Both kinds are ``CORRECT``; which one it was is recoverable from
    #: the label's ``expectation``, and the split matters only to the
    #: denominators of EVA-17.
    CORRECT = "correct"

    INCORRECT = "incorrect"
    MISSING = "missing"
    SPURIOUS = "spurious"
    UNLABELED = "unlabeled"
    UNEVALUATED = "unevaluated"


class FieldOutcome(BaseModel):
    """One comparison of one prediction against one label (EVA-15)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    field_path: str
    kind: FieldOutcomeKind

    #: Which tier the document belongs to. Present on the outcome because
    #: redaction follows the tier, not the caller (EVA-25), and because the
    #: report's total order leads with it.
    tier: Tier

    #: Canonical renderings. ``None`` when the outcome is ``CORRECT`` -- there is
    #: no near-miss to diagnose -- or when redacted.
    expected: str | None = None
    predicted: str | None = None

    #: Set instead of the values under a disclosure-restricted tier (EVA-25).
    #: FR-026's "record the expected and the predicted value" is satisfied by the
    #: hash under that rule: a restricted near-miss is diagnosable as *different*
    #: but not *how*, which is the trade the dataset's terms impose.
    expected_hash: str | None = None
    predicted_hash: str | None = None
    redacted: bool = False

    #: Which versioned rule decided this comparison.
    comparator_version: str | None = None

    #: **Copied** from Milestone 4, never recomputed (FR-033). Whether a value is
    #: correct and whether it is grounded are independent facts, measured
    #: separately and reported as separate numbers: a correct but ungrounded value
    #: is ``CORRECT`` here and ``ungrounded`` in the grounding rate (EVA-15b).
    grounding_status: GroundingStatus | None = None

    #: Also copied. **Never averaged across tiers** -- an exact score is ``1.0``
    #: by definition while a fuzzy score is a measured similarity, so a mean over
    #: both is a number with no meaning (ADR-0004, FR-039).
    grounding_score: float | None = None

    #: ``None`` when the label states no expected location.
    location_agreement: LocationAgreement | None = None

    #: Nothing on this model reads ``model_confidence``. It is untrusted upstream
    #: by ADR-0004 and stays untrusted here (FR-028); it is not carried, not
    #: compared, and moves no metric.
