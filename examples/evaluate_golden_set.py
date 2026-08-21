"""Score the committed golden set and print every metric with its terms.

Runs standalone with no credentials, no network, no database, and no object
storage:

    uv run python examples/evaluate_golden_set.py

No ``--extra google``, no ``--extra azure``, no ``--extra pdf``. The predictions
under ``datasets/mvp/predictions/`` were recorded once with the ``echo`` adapter
and committed, so scoring them is a replay — which is what makes this the
30-second version rather than a setup guide.

**Watch the denominators.** Every number below prints the numerator and
denominator it came from (FR-029), and a metric with an empty denominator prints
``undefined`` rather than ``0.00`` (FR-032). A rate of zero reads as total
failure; an unasked question is not one, and the difference is the whole reason
those two are never collapsed.
"""

from __future__ import annotations

from pathlib import Path

from docdoc.evaluation import (
    MetricValue,
    evaluate,
    load_golden_set,
    load_prediction_set,
    schema_facts,
)
from docdoc.extraction import SchemaRegistry

REPO = Path(__file__).resolve().parent.parent
DATASET = REPO / "datasets" / "mvp"

#: The five Principle IX requires. The report carries three more beside them;
#: these are the ones a quality gate reads.
HEADLINE = ("field_accuracy", "coverage", "missing_rate", "incorrect_rate", "grounding_rate")


def render(metric: MetricValue) -> str:
    """One metric, with the terms that let a reader check it by hand."""
    value = "undefined" if metric.value is None else f"{metric.value:.4f}"
    return f"{metric.name:<18} {value:>10}   ({metric.numerator}/{metric.denominator})"


def main() -> int:
    registry = SchemaRegistry.from_paths([REPO / "schemas"])
    facts = schema_facts([registry.describe(identity) for identity in registry.identities()])

    golden = load_golden_set(DATASET / "manifest.json", facts=facts)
    predictions = load_prediction_set(DATASET / "predictions", facts=facts)

    report = evaluate(golden, predictions, facts=facts)

    # Read off the report rather than the golden set. FR-009 requires every report
    # to carry its dataset's size for exactly this reason: a reader holding only a
    # report must be able to tell how much was measured, and the constitution's
    # fifth quality gate turns blocking at a target size.
    print("Golden set")
    for size in report.dataset_size:
        print(
            f"  {size.tier:<12} {size.documents} documents, {size.labelled_fields} labelled fields"
        )
    print("  (gate 5 targets 50 documents / 500 labelled fields, public tier; still advisory)")

    print("\nMetrics (micro - one field, one vote)")
    for name in HEADLINE:
        print("  " + render(report.metrics.micro[name]))

    print("\nMetrics (macro - one document, one vote)")
    for name in HEADLINE:
        metric = report.metrics.macro[name]
        # The counts travel with the number: a document whose own metric is
        # undefined cannot enter a mean, and excluding it silently would make the
        # macro number describe an unstated subset (EVA-18a).
        print(
            "  "
            + render(metric)
            + f"   averaged over {metric.documents_averaged}, "
            + f"{metric.documents_undefined} undefined"
        )

    print("\nOutcomes")
    counts = report.metrics.counts
    print(f"  correct {counts.correct}, incorrect {counts.incorrect}, missing {counts.missing}")
    print(f"  spurious {counts.spurious}, unevaluated {counts.unevaluated}")
    # Reported, and in no accuracy denominator: a prediction the golden set says
    # nothing about is neither right nor wrong (FR-036).
    print(f"  unlabeled {counts.unlabeled} (counted, and in no denominator)")

    # Two facts, not one. The verdicts say how many documents came out invalid;
    # only the counts say how many checks ran and how many could not be evaluated
    # at all. Both are Milestone 5's, reused rather than recomputed (FR-034).
    validation = report.metrics.validation_counts
    print("\nValidation (Milestone 5's, reused)")
    print(f"  verdicts {report.metrics.validation_verdicts}")
    if validation is None:
        print("  no document carried a validation result")
    else:
        print(
            f"  {validation.declared} checks declared, {validation.passed} passed, "
            f"{validation.failed} failed, {validation.not_evaluated} not evaluated"
        )

    print("\nWhat did not agree")
    for outcome in report.outcomes:
        if str(outcome.kind) in {"correct", "unlabeled"}:
            continue
        print(
            f"  {outcome.document_id}/{outcome.field_path}: {outcome.kind} - "
            f"expected {outcome.expected!r}, predicted {outcome.predicted!r}"
        )

    if report.partial is not None:
        partial = report.partial
        print("\nPartial run")
        print(f"  skipped tiers: {', '.join(str(t) for t in partial.skipped_tiers)}")
        print(f"  covered {partial.covered_labels} of {partial.declared_labels} declared labels")
        print("  the restricted tier is referenced by hash and never committed (ADR-0009)")

    print(f"\nreport_id  {report.report_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
