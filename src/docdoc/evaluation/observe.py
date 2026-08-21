"""One structured event per run, and nothing that could leak content.

The boundary Milestones 3, 4, and 5 set holds here, and this stage tests it as
hard as validation did. **Outcomes carry values by design** -- "expected 1249.00,
predicted 1240.00" is what makes a near-miss diagnosable, and FR-026 requires it.
**Logs carry identities, versions, counts, and hashes only** (FR-057).

"Values never appear anywhere" is the wrong reading of FR-057 and would make
outcomes useless, so the rule is restated in the module most able to blur it.

Emitted on every path, including refusal. A refused run has no report to log a
``report_id`` from, so it logs the two identities that explain the refusal --
which for this stage is the whole diagnosis of its most common failure, a schema
identity that does not match the labels.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docdoc.evaluation.report import EvaluationReport

__all__ = ["EVENT_NAME", "log_evaluation", "log_refusal"]

EVENT_NAME = "evaluation.evaluate"

_logger = logging.getLogger("docdoc.evaluation")


def log_evaluation(report: EvaluationReport, *, duration_ms: float) -> None:
    """Emit the success event.

    ``partial`` sits next to the metrics deliberately. A dashboard reading
    accuracy without it would report a healthy system that had measured a
    fraction of the dataset and never said so.
    """
    provenance = report.provenance
    counts = report.metrics.counts
    payload: dict[str, Any] = {
        "event": EVENT_NAME,
        "outcome": "ok",
        "report_id": report.report_id,
        "golden_set_id": provenance.golden_set_id,
        "prediction_set_id": provenance.prediction_set_id,
        "scorer_id": provenance.scorer_id,
        "scorer_version": provenance.scorer_version,
        "metric_definition_version": provenance.metric_definition_version,
        "entry_alignment_version": provenance.entry_alignment_version,
        "location_rule_version": provenance.location_rule_version,
        "documents": len(report.document_scores),
        "labelled_fields": counts.labelled,
        "correct": counts.correct,
        "incorrect": counts.incorrect,
        "missing": counts.missing,
        "spurious": counts.spurious,
        "unlabeled": counts.unlabeled,
        "unevaluated": counts.unevaluated,
        "partial": report.partial is not None,
        "redacted_tiers": [str(tier) for tier in report.redacted_tiers],
        "duration_ms": round(duration_ms, 3),
    }
    if report.partial is not None:
        payload["covered_labels"] = report.partial.covered_labels
        payload["declared_labels"] = report.partial.declared_labels
    _logger.info(EVENT_NAME, extra={"docdoc": payload})


def log_refusal(
    *,
    reason: str,
    golden_set_id: str | None = None,
    prediction_set_id: str | None = None,
    document_id: str | None = None,
    expected: str | None = None,
    actual: str | None = None,
    duration_ms: float = 0.0,
) -> None:
    """Emit the refusal event.

    ``expected`` and ``actual`` here are **identities and versions**, never
    document text or field values: the refusals this stage raises are about
    schema identities, artifact ids, and declared counts.
    """
    payload: dict[str, Any] = {
        "event": EVENT_NAME,
        "outcome": "refused",
        "reason": reason,
        "golden_set_id": golden_set_id,
        "prediction_set_id": prediction_set_id,
        "document_id": document_id,
        "expected": expected,
        "actual": actual,
        "duration_ms": round(duration_ms, 3),
    }
    _logger.warning(EVENT_NAME, extra={"docdoc": payload})
