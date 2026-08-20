"""The walk that turns labels and predictions into outcomes.

For each document in the golden set, every label resolves to exactly one member of
the closed set, every predicted path the golden set does not label becomes
``UNLABELED``, and a document with no prediction at all becomes ``UNEVALUATED``.

The three cases that decide whether this feature is honest:

**A document with no prediction is ``UNEVALUATED`` and stays in every
denominator** (FR-005). Dropping it is how a crash becomes an accuracy
improvement.

**A document that failed mid-pipeline has its labelled fields counted as
``MISSING``** (FR-037), not excluded. Same reason, one step further along: failing
on the hard documents must not be able to raise a score.

**A value that is correct but ungrounded is ``CORRECT``** (FR-027). Correctness
and groundedness are independent facts measured separately; merging them would let
a grounding failure masquerade as an extraction failure.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docdoc.evaluation.comparators import comparator_version_for, equal
from docdoc.evaluation.labels import Expectation
from docdoc.evaluation.location import agreement_for
from docdoc.evaluation.ordering import path_key
from docdoc.evaluation.outcomes import FieldOutcome, FieldOutcomeKind
from docdoc.evaluation.redact import redact_outcome

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docdoc.evaluation.golden import GoldenDocument, GoldenSet
    from docdoc.evaluation.labels import Label
    from docdoc.evaluation.predictions import DocumentPrediction
    from docdoc.extraction.value import ExtractedValue
    from docdoc.grounding.result import GroundingOutcome

__all__ = ["outcome_sort_key", "score_document"]


def outcome_sort_key(outcome: FieldOutcome) -> tuple[Any, ...]:
    """The total order of EVA-26: ``(tier, document_id, path_key, field_path)``.

    ``field_path`` is the final tie-break so the order stays total even if two
    distinct paths ever decompose identically.
    """
    return (
        str(outcome.tier),
        outcome.document_id,
        path_key(outcome.field_path),
        outcome.field_path,
    )


def _render(value: Any) -> str | None:
    """A canonical rendering, so two runs produce the same text for one fact."""
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _predicted_values(prediction: DocumentPrediction) -> dict[str, ExtractedValue]:
    """Every scalar the extraction recorded, keyed by field path.

    Walks the value tree the same way `conform` built it, so the paths here are
    the paths a label addresses (FR-017).
    """
    if prediction.extraction is None:
        return {}
    found: dict[str, ExtractedValue] = {}
    _walk(prediction.extraction.values, "", found)
    return found


def _walk(node: Any, prefix: str, found: dict[str, ExtractedValue]) -> None:
    if isinstance(node, dict):
        for name, child in node.items():
            _walk(child, f"{prefix}{name}" if not prefix else f"{prefix}.{name}", found)
        return
    if isinstance(node, (list, tuple)):
        for index, child in enumerate(node):
            _walk(child, f"{prefix}[{index}]", found)
        return
    if hasattr(node, "field_path"):
        found[node.field_path] = node


def _grounding_outcome(prediction: DocumentPrediction, field_path: str) -> GroundingOutcome | None:
    if prediction.grounding is None:
        return None
    return prediction.grounding.outcomes.get(field_path)


def _classify(
    label: Label,
    predicted: ExtractedValue | None,
    *,
    failed: bool,
) -> tuple[FieldOutcomeKind, Any, Any]:
    """Resolve one label against one prediction to exactly one closed outcome."""
    if label.expectation is Expectation.VALUE:
        if failed or predicted is None or not predicted.present:
            return FieldOutcomeKind.MISSING, label.value, None
        if equal(label.value, predicted.value):
            return FieldOutcomeKind.CORRECT, label.value, predicted.value
        return FieldOutcomeKind.INCORRECT, label.value, predicted.value

    # An absence label. A failed document did not assert anything, so it cannot
    # have invented a value: absence is trivially satisfied and counted correct.
    if failed or predicted is None or not predicted.present:
        return FieldOutcomeKind.CORRECT, None, None
    return FieldOutcomeKind.SPURIOUS, None, predicted.value


def score_document(
    document: GoldenDocument,
    labels: Sequence[Label],
    prediction: DocumentPrediction | None,
    *,
    field_type_for: dict[str, Any] | None = None,
) -> tuple[FieldOutcome, ...]:
    """Every outcome for one document, in no particular order.

    The caller sorts: ordering is a property of the report, not of a document.
    """
    types = field_type_for or {}

    if prediction is None:
        # No prediction at all. Named, counted, and in every denominator -- the
        # distinction from a *failed* document is real and both are reported.
        return tuple(
            redact_outcome(
                FieldOutcome(
                    document_id=document.document_id,
                    field_path=label.field_path,
                    kind=FieldOutcomeKind.UNEVALUATED,
                    tier=document.tier,
                    expected=_render(label.value),
                )
            )
            for label in labels
        )

    failed = not prediction.processed
    predicted_values = _predicted_values(prediction)
    labelled_paths = {label.field_path for label in labels}
    outcomes: list[FieldOutcome] = []

    for label in labels:
        predicted = predicted_values.get(label.field_path)
        kind, expected_value, predicted_value = _classify(label, predicted, failed=failed)
        grounding = _grounding_outcome(prediction, label.field_path)

        outcomes.append(
            redact_outcome(
                FieldOutcome(
                    document_id=document.document_id,
                    field_path=label.field_path,
                    kind=kind,
                    tier=document.tier,
                    expected=None if kind is FieldOutcomeKind.CORRECT else _render(expected_value),
                    predicted=None
                    if kind is FieldOutcomeKind.CORRECT
                    else _render(predicted_value),
                    comparator_version=comparator_version_for(types.get(label.field_path)),
                    grounding_status=None if grounding is None else grounding.status,
                    grounding_score=None if grounding is None else grounding.score,
                    location_agreement=agreement_for(label.location, grounding),
                )
            )
        )

    # Predicted paths the golden set says nothing about. Counted and reported,
    # and in no accuracy denominator (FR-036, EVA-8b).
    for field_path, value in predicted_values.items():
        if field_path in labelled_paths or not value.present:
            continue
        grounding = _grounding_outcome(prediction, field_path)
        outcomes.append(
            redact_outcome(
                FieldOutcome(
                    document_id=document.document_id,
                    field_path=field_path,
                    kind=FieldOutcomeKind.UNLABELED,
                    tier=document.tier,
                    predicted=_render(value.value),
                    grounding_status=None if grounding is None else grounding.status,
                    grounding_score=None if grounding is None else grounding.score,
                )
            )
        )

    return tuple(outcomes)


def value_path_index(golden: GoldenSet) -> set[tuple[str, str]]:
    """``(document_id, field_path)`` for every label stating a value.

    The V partition of EVA-17. ``CORRECT`` and ``UNEVALUATED`` occur in both V and
    A with different denominators, so counting needs to know which partition each
    outcome belongs to.
    """
    return {
        (document_id, label.field_path)
        for document_id, labels in golden.labels.items()
        for label in labels
        if label.expectation is Expectation.VALUE
    }
