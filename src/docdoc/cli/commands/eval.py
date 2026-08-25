"""``docdoc eval MANIFEST --predictions DIR`` — score a golden set.

The fifth of the five commands FR-026 names, and the one promised as
``docdoc eval ./dataset`` at the project's founding.

**It computes nothing.** Every number here is :mod:`docdoc.evaluation`'s, read off
the report it returned. ``examples/evaluate_golden_set.py`` printed these same
numbers before this command existed, and an integration test asserts the two
still agree — because the moment a CLI starts deriving a metric, there are two
definitions of field accuracy and the published one is whichever the reader
happened to run.

**A partial run says so.** ADR-0009 requires that a run without the restricted
tier produce a report *marked partial*, naming what it skipped, and never a
smaller full one. That declaration is carried into both output forms rather than
being left in the report object, because the whole point of the marking is that
somebody downstream sees it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from docdoc.cli.render import Rendering

if TYPE_CHECKING:
    import argparse

    from docdoc.cli.config import Settings
    from docdoc.evaluation import EvaluationReport, MetricValue

__all__ = ["HEADLINE", "run"]

#: The five Principle IX requires. The report carries more beside them; these are
#: the ones a quality gate reads.
HEADLINE = ("field_accuracy", "coverage", "missing_rate", "incorrect_rate", "grounding_rate")


def run(args: argparse.Namespace, settings: Settings) -> Rendering:
    """Score a prediction set against a golden set and report every metric."""
    from docdoc.evaluation import (
        evaluate,
        load_golden_set,
        load_prediction_set,
        schema_facts,
    )

    registry = settings.registry()
    facts = schema_facts([registry.describe(identity) for identity in registry.identities()])

    golden = load_golden_set(Path(args.manifest), facts=facts)
    predictions = load_prediction_set(Path(args.predictions), facts=facts)
    report = evaluate(golden, predictions, facts=facts)

    return Rendering(code=0, data=_data(report), lines=_lines(report))


def _data(report: EvaluationReport) -> dict[str, Any]:
    metrics = report.metrics
    return {
        "report_id": report.report_id,
        "partial": None if report.partial is None else report.partial.model_dump(mode="json"),
        "dataset_size": [size.model_dump(mode="json") for size in report.dataset_size],
        "metrics": {
            "micro": {name: _metric(metrics.micro[name]) for name in HEADLINE},
            "macro": {name: _metric(metrics.macro[name]) for name in HEADLINE},
        },
        "counts": metrics.counts.model_dump(mode="json"),
        "validation_verdicts": dict(metrics.validation_verdicts),
        "disagreements": [
            {
                "document_id": outcome.document_id,
                "field_path": outcome.field_path,
                "kind": str(outcome.kind),
                "expected": outcome.expected,
                "predicted": outcome.predicted,
            }
            for outcome in report.outcomes
            if str(outcome.kind) not in {"correct", "unlabeled"}
        ],
        "provenance": report.provenance.model_dump(mode="json"),
    }


def _metric(metric: MetricValue) -> dict[str, Any]:
    """One metric with the terms it came from.

    ``None`` rather than ``0.0`` for an empty denominator, all the way into the
    JSON: a rate of zero reads as total failure and an unasked question is not
    one. The evaluation layer already refuses to collapse those two and this
    refuses to collapse them back.
    """
    return {
        "value": metric.value,
        "numerator": metric.numerator,
        "denominator": metric.denominator,
    }


def _lines(report: EvaluationReport) -> list[str]:
    lines = ["Golden set"]
    for size in report.dataset_size:
        lines.append(
            f"  {size.tier:<12} {size.documents} documents, {size.labelled_fields} labelled fields"
        )

    lines.append("")
    lines.append("Metrics (micro — one field, one vote)")
    lines.extend(f"  {_render(report.metrics.micro[name])}" for name in HEADLINE)

    lines.append("")
    lines.append("Metrics (macro — one document, one vote)")
    for name in HEADLINE:
        metric = report.metrics.macro[name]
        lines.append(
            f"  {_render(metric)}   averaged over {metric.documents_averaged}, "
            f"{metric.documents_undefined} undefined"
        )

    counts = report.metrics.counts
    lines.extend(
        [
            "",
            "Outcomes",
            f"  correct {counts.correct}, incorrect {counts.incorrect}, missing {counts.missing}",
            f"  spurious {counts.spurious}, unevaluated {counts.unevaluated}",
            f"  unlabeled {counts.unlabeled} (counted, and in no denominator)",
        ]
    )

    if report.partial is not None:
        partial = report.partial
        lines.extend(
            [
                "",
                "PARTIAL RUN (ADR-0009)",
                f"  skipped tiers: {', '.join(str(tier) for tier in partial.skipped_tiers)}",
                f"  covered {partial.covered_labels} of {partial.declared_labels} declared labels",
            ]
        )

    lines.extend(["", f"report_id  {report.report_id}"])
    return lines


def _render(metric: MetricValue) -> str:
    value = "undefined" if metric.value is None else f"{metric.value:.4f}"
    return f"{metric.name:<18} {value:>10}   ({metric.numerator}/{metric.denominator})"
