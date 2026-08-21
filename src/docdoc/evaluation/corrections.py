"""A human correction, and the explicit act that turns one into dataset signal.

A reviewer looking at a report sees a wrong value and knows the right one. That
knowledge is worth more than the correction of one field, and it is routinely
lost: it goes into a spreadsheet, or a ticket, or a conversation, and the next
evaluation measures against the same stale labels.

So a correction is a **model**: it names the field, both values, where in the
document the right one is, why, who said so, and when. Those seven are what the
constitution requires, and each is load-bearing rather than ceremonial -- a
correction without a reason cannot be reviewed, and one without an annotator
cannot be weighed against a disagreeing second opinion.

Two properties decide whether this is trustworthy.

**A correction alters nothing it annotates** (FR-052). Not the extraction, not
the grounding, not the validation result. It sits beside them. If recording a
correction edited the artifact it described, the recorded pipeline output would
become a function of who reviewed it, and the whole chain of ADR-0003 identities
would be describing a run that never happened.

**It moves no metric until promoted** (FR-053). Promotion is a separate,
explicit act that returns a **new** golden set with a **new** ``golden_set_id``.
That is not caution for its own sake: reports either side of a promotion are then
not comparable without the difference being visible, which is FR-046 doing the
same job from the other end. A correction that silently entered the dataset would
move every historical number and explain none of it.

**What this deliberately is not** (FR-054): a review interface, an assignment
system, a workflow, a queue, or a storage service. Principle IX permits
corrections as a model and forbids the MVP becoming a review platform. Where
corrections live between being recorded and being promoted is the caller's
decision -- a JSON file is a perfectly good answer, and it is one this package
does not need to know about.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, model_validator

from docdoc.evaluation.errors import EvaluationError, naming
from docdoc.evaluation.golden import GoldenSet
from docdoc.evaluation.labels import Expectation, ExpectedLocation, Label

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = ["Correction", "promote"]


class Correction(BaseModel):
    """One reviewer's statement that a recorded value was wrong (EVA-29)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Which run and which result this corrects. Without both, a correction can
    #: be read as a correction of a *different* version's output -- and applied
    #: to labels it was never about (FR-051).
    report_id: str
    document_id: str

    # -- the seven the constitution requires ---------------------------------

    field_path: str

    #: What the pipeline produced, as the report rendered it. Kept so a promotion
    #: can be reviewed later against what it was correcting, rather than against
    #: whatever the pipeline produces by then.
    predicted_value: Any = None

    #: What the reviewer says is right. ``None`` with
    #: ``corrected_absence=True`` asserts the field is correctly absent -- the
    #: two things a label can say, so a correction can express either.
    corrected_value: Any = None

    #: Where in the document the right value is. A page, optionally narrowed by a
    #: box; never a text offset, for the reason EVA-7a gives.
    location: ExpectedLocation | None = None

    #: Why. Free text, and required: a correction nobody explained cannot be
    #: reviewed, and an unreviewable correction is an assertion with a name on it.
    reason: str

    annotator: str
    timestamp: datetime

    #: Whether this corrects *to an absence* rather than to a value.
    corrected_absence: bool = False

    @model_validator(mode="after")
    def _states_something(self) -> Correction:
        if self.corrected_absence and self.corrected_value is not None:
            raise ValueError(
                f"correction for {self.field_path!r} asserts the field is absent but "
                f"also carries the value {self.corrected_value!r}"
            )
        if not self.corrected_absence and self.corrected_value is None:
            raise ValueError(
                f"correction for {self.field_path!r} states no corrected value; set "
                "corrected_absence=True to assert the field is correctly absent"
            )
        if not self.reason.strip():
            raise ValueError(
                f"correction for {self.field_path!r} states no reason. A correction "
                "nobody explained cannot be reviewed, and an unreviewable correction "
                "is an assertion with a name on it"
            )
        if not self.annotator.strip():
            raise ValueError(f"correction for {self.field_path!r} names no annotator")
        return self

    def as_label(self) -> Label:
        """The label this correction would become, if promoted.

        ``labeler`` and ``labeled_at`` carry the annotator and the timestamp, so
        a promoted label keeps the attribution -- and neither enters
        ``golden_set_id``, because neither can change a metric (EVA-6).
        """
        return Label(
            field_path=self.field_path,
            expectation=Expectation.ABSENT if self.corrected_absence else Expectation.VALUE,
            value=None if self.corrected_absence else self.corrected_value,
            location=self.location,
            labeler=self.annotator,
            labeled_at=self.timestamp,
        )


def promote(golden: GoldenSet, corrections: Iterable[Correction]) -> GoldenSet:
    """Fold corrections into a **new** golden set, leaving the original untouched.

    A promoted correction replaces the label at its field path, or adds one where
    the dataset had none -- the second case being how a reviewer closes a gap that
    ``UNLABELED`` exposed.

    The returned set carries a **cleared** ``golden_set_id``, so the next
    :func:`~docdoc.evaluation.evaluate` recomputes it and reports scored against
    it are visibly incomparable with reports scored against the original. That
    visibility is the point: a dataset that changed silently would move every
    historical number and explain none of them (FR-053).

    Raises:
        EvaluationError: a correction names a document the golden set does not
            contain. The two do not describe the same thing, and applying it
            would be an edit nobody asked for.
    """
    grouped: dict[str, list[Correction]] = {}
    with naming(golden.golden_set_id or None):
        for correction in corrections:
            if golden.document(correction.document_id) is None:
                raise EvaluationError(
                    f"correction names document {correction.document_id!r}, which "
                    "the golden set does not contain",
                    document_id=correction.document_id,
                    field_path=correction.field_path,
                )
            grouped.setdefault(correction.document_id, []).append(correction)

    if not grouped:
        return golden

    labels = dict(golden.labels)
    for document_id, items in grouped.items():
        existing = {label.field_path: label for label in labels.get(document_id, ())}
        for correction in items:
            existing[correction.field_path] = correction.as_label()
        labels[document_id] = tuple(sorted(existing.values(), key=lambda label: label.field_path))

    # The declared count follows the labels. A promotion that added a label and
    # left the declaration behind would be refused at the next load as a bundle
    # short of -- or over -- its declaration, which is the right check firing at
    # the wrong moment and blaming the wrong person.
    documents = tuple(
        document.model_copy(update={"declared_label_count": len(labels[document.document_id])})
        if document.document_id in grouped
        else document
        for document in golden.documents
    )

    return GoldenSet(
        documents=documents,
        labels=labels,
        entry_keys=golden.entry_keys,
        golden_set_id="",
    )
