"""T044 — an empty denominator reports ``None``, never ``0.0`` (FR-032, SC-005).

A rate of zero and an unasked question are different facts, and only one of them
is bad news. "Mislocation rate: 0.00" reads as *nothing was mislocated*. If the
dataset states no expected locations at all, the true answer is *we did not ask*,
and printing `0.00` turns a gap in the dataset into a claim about the pipeline.

The direction of the lie is what makes this worth its own file. Every one of
these substitutions flatters:

- ``field_accuracy`` of ``0.0`` on an empty dataset reads as total failure, which
  at least gets investigated;
- ``missing_rate`` of ``0.0`` reads as perfect, and does not;
- ``mislocation_rate`` of ``0.0`` reads as perfect, and does not.

Two of the three are silently reassuring, and a dashboard cannot tell them from
the real thing. ``None`` cannot be mistaken for either.
"""

from __future__ import annotations

import pytest

from docdoc.evaluation import (
    GoldenSet,
    PredictionSet,
    evaluate,
)
from docdoc.evaluation.metrics import MetricValue, OutcomeCounts, _metrics_from
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set


def test_a_zero_denominator_yields_none() -> None:
    """The rule, at the one place it is implemented."""
    metrics = _metrics_from(OutcomeCounts(), None)

    for name, metric in metrics.items():
        assert metric.denominator == 0, name
        assert metric.value is None, f"{name} reported {metric.value!r} for an empty denominator"
        assert metric.value != 0.0


def test_none_is_not_zero_and_the_difference_is_visible() -> None:
    """A real zero must still be reported as zero. The check has to discriminate."""
    counts = OutcomeCounts(correct_value=0, incorrect=0, missing=4)
    metrics = _metrics_from(counts, None)

    assert metrics["coverage"].value == 0.0, "0 of 4 answered is a real, earned zero"
    assert metrics["coverage"].denominator == 4
    assert metrics["spurious_rate"].value is None, "no absence labels: nothing was asked"


def test_an_empty_golden_set_reports_no_metric_as_zero() -> None:
    """The degenerate case a reader is most likely to meet first."""
    empty = GoldenSet(documents=(), labels={})
    report = evaluate(empty, PredictionSet(predictions={}))

    for name, metric in report.metrics.micro.items():
        assert metric.value is None, f"{name} is {metric.value!r} over an empty dataset"

    assert report.metrics.counts.labelled == 0


def test_a_golden_set_where_every_field_is_unlabeled_reports_none() -> None:
    """Predictions with nothing to check them against.

    This is the case that would otherwise read as a catastrophic accuracy drop:
    every prediction present, nothing labelled, so every denominator empty. The
    honest report says nothing was measured.
    """
    golden = golden_set()
    unlabelled = golden.model_copy(update={"labels": {}})

    report = evaluate(unlabelled, prediction_set(), facts=facts_for_fixtures())

    assert report.metrics.counts.labelled == 0
    assert report.metrics.counts.unlabeled > 0, "the predictions are still there"
    for name in ("field_accuracy", "coverage", "missing_rate", "incorrect_rate"):
        assert report.metrics.micro[name].value is None, name


def test_a_dataset_with_no_expected_locations_reports_no_mislocation_rate() -> None:
    """The most flattering of the substitutions, tested on its own."""
    golden = golden_set()
    stripped = {
        document_id: tuple(label.model_copy(update={"location": None}) for label in labels)
        for document_id, labels in golden.labels.items()
    }

    report = evaluate(
        golden.model_copy(update={"labels": stripped}),
        prediction_set(),
        facts=facts_for_fixtures(),
    )

    mislocation = report.metrics.micro["mislocation_rate"]
    assert mislocation.denominator == 0
    assert mislocation.value is None, (
        "reporting 0.00 here would say nothing was mislocated, when what happened "
        "is that no label stated where anything should be"
    )


def test_no_metric_anywhere_in_a_report_reports_zero_for_an_empty_denominator() -> None:
    """Swept across every level: dataset micro, macro, per document, per field path.

    The rule is easy to hold in one code path and lose in another, and the macro
    path in particular divides by a different thing.
    """
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())

    everywhere: list[tuple[str, MetricValue]] = []
    everywhere += [(f"micro.{k}", v) for k, v in report.metrics.micro.items()]
    everywhere += [(f"macro.{k}", v) for k, v in report.metrics.macro.items()]
    for score in report.document_scores:
        everywhere += [(f"{score.document_id}.{k}", v) for k, v in score.metrics.items()]
    for path, metrics in report.metrics.per_field_path.items():
        everywhere += [(f"{path}.{k}", v) for k, v in metrics.items()]

    offenders = [
        name for name, metric in everywhere if metric.denominator == 0 and metric.value == 0.0
    ]
    assert not offenders, f"these reported 0.0 for an empty denominator: {offenders}"

    undefined = [name for name, metric in everywhere if metric.value is None]
    assert undefined, (
        "no metric anywhere in this report is undefined, so the fixture cannot show "
        "that undefined is reachable at all"
    )


def test_the_defined_property_says_which_one_it_is() -> None:
    """A consumer should not have to compare against ``None`` by hand."""
    defined = MetricValue(name="x", value=0.0, numerator=0, denominator=4)
    undefined = MetricValue(name="x", value=None, numerator=0, denominator=0)

    assert defined.defined
    assert not undefined.defined


@pytest.mark.parametrize("name", ["field_accuracy", "coverage", "missing_rate", "grounding_rate"])
def test_an_undefined_metric_still_states_its_terms(name: str) -> None:
    """``None`` with no denominator would be a mystery rather than an explanation."""
    metric = _metrics_from(OutcomeCounts(), None)[name]

    assert metric.value is None
    assert metric.numerator == 0
    assert metric.denominator == 0
    assert metric.name == name
