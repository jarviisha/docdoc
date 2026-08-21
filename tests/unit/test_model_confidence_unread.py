"""T055 — ``model_confidence`` moves no outcome, no metric, and no identity (FR-028).

ADR-0004 records it as the model's own self-report and keeps it untrusted: it
routes nothing, gates nothing, and is not a quality metric. Milestone 3 stores it
verbatim so a later calibrator can be fitted against it, and that is all.

Untrusted upstream, untrusted here. The temptation at this layer is specific and
plausible: weight the accuracy by confidence, or exclude low-confidence fields
from the denominator. Both make the score a function of what the model says about
itself -- so a model that learned to report high confidence would score better
without extracting anything more correctly. The metric would measure the model's
self-assessment, which is the one thing it has every incentive to inflate.

The test sweeps the whole range rather than testing one value, because a
threshold anywhere in ``[0, 1]`` is the shape this failure would take.
"""

from __future__ import annotations

from typing import Any

import pytest

from docdoc.evaluation import evaluate
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set

CONFIDENCES = [0.0, 0.01, 0.25, 0.49, 0.5, 0.51, 0.75, 0.9, 0.99, 1.0, None]


def _with_confidence(prediction: Any, confidence: float | None) -> Any:
    """Rewrite every extracted value's ``model_confidence``, changing nothing else."""

    def rewrite(node: Any) -> Any:
        if hasattr(node, "field_path"):
            return node.model_copy(update={"model_confidence": confidence})
        if isinstance(node, dict):
            return {name: rewrite(child) for name, child in node.items()}
        if isinstance(node, (list, tuple)):
            return tuple(rewrite(child) for child in node)
        return node

    extraction = prediction.extraction
    if extraction is None:
        return prediction
    return prediction.model_copy(
        update={"extraction": extraction.model_copy(update={"values": rewrite(extraction.values)})}
    )


def _predictions_with(confidence: float | None):  # type: ignore[no-untyped-def]
    base = prediction_set()
    return base.model_copy(
        update={
            "predictions": {
                document_id: _with_confidence(prediction, confidence)
                for document_id, prediction in base.predictions.items()
            }
        }
    )


@pytest.fixture(scope="module")
def baseline():  # type: ignore[no-untyped-def]
    return evaluate(golden_set(), _predictions_with(0.5), facts=facts_for_fixtures())


@pytest.mark.parametrize("confidence", CONFIDENCES)
def test_no_metric_moves_across_the_whole_confidence_range(
    baseline, confidence: float | None
) -> None:  # type: ignore[no-untyped-def]
    """Every metric, at every level, identical."""
    report = evaluate(golden_set(), _predictions_with(confidence), facts=facts_for_fixtures())

    assert report.metrics.micro == baseline.metrics.micro
    assert report.metrics.macro == baseline.metrics.macro
    assert report.metrics.per_field_path == baseline.metrics.per_field_path
    assert report.metrics.counts == baseline.metrics.counts


@pytest.mark.parametrize("confidence", CONFIDENCES)
def test_no_outcome_moves(baseline, confidence: float | None) -> None:  # type: ignore[no-untyped-def]
    report = evaluate(golden_set(), _predictions_with(confidence), facts=facts_for_fixtures())

    assert report.outcomes == baseline.outcomes


@pytest.mark.parametrize("confidence", CONFIDENCES)
def test_the_report_id_does_not_move(baseline, confidence: float | None) -> None:  # type: ignore[no-untyped-def]
    """FR-042 from the other side: an identity must not move on something unread.

    ``report_id`` moving here would make two reports incomparable because the
    model felt differently about the same answer.
    """
    report = evaluate(golden_set(), _predictions_with(confidence), facts=facts_for_fixtures())

    assert report.report_id == baseline.report_id


def test_the_rewrite_actually_changes_the_predictions() -> None:
    """The guard on the guard.

    If the helper silently failed to set anything, every assertion above would
    compare a report against an identical one and pass while testing nothing.
    """
    low = _predictions_with(0.0).for_document("clean")
    high = _predictions_with(1.0).for_document("clean")
    assert low is not None
    assert high is not None
    assert low.extraction is not None
    assert high.extraction is not None

    assert low.extraction.values["total"].model_confidence == 0.0
    assert high.extraction.values["total"].model_confidence == 1.0
    assert low.extraction.values["total"].value == high.extraction.values["total"].value


def test_no_outcome_carries_model_confidence_at_all() -> None:
    """It is not merely unread -- it is not carried (EVA-15c).

    A field on the outcome would be an invitation: the next contributor sees it,
    assumes it is there to be used, and the trust boundary is gone.
    """
    from docdoc.evaluation.outcomes import FieldOutcome

    assert "model_confidence" not in FieldOutcome.model_fields
    assert "calibrated_confidence" not in FieldOutcome.model_fields


def test_the_scorer_source_never_mentions_it() -> None:
    """Cheap, and it catches the read that a fixture happens not to exercise."""
    import pathlib

    import docdoc.evaluation

    root = pathlib.Path(docdoc.evaluation.__file__).parent
    offenders = [
        path.name
        for path in sorted(root.rglob("*.py"))
        if "model_confidence" in path.read_text(encoding="utf-8")
        and path.name not in {"outcomes.py", "__init__.py"}
    ]

    assert not offenders, (
        f"{offenders} mention model_confidence. The only permitted mentions are the "
        "comments in outcomes.py and __init__.py that say it is not read"
    )
