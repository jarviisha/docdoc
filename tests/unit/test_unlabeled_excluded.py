"""T036 — a predicted field nobody labelled is counted and judged nowhere (FR-036, SC-007).

``UNLABELED`` is the third state, and it is neither of the two a reader expects.
Both alternatives are worse in the same direction:

- **Assume it correct**, and accuracy rises whenever the model returns more
  fields, regardless of whether they are right.
- **Assume it wrong**, and accuracy falls whenever the dataset is incompletely
  labelled, which every dataset always is.

Either way accuracy becomes a function of how completely the golden set happens to
be labelled rather than of how well the pipeline performed. So the outcome is
reported -- a maintainer should see it, it is often the signal that a label is
missing -- and it enters **zero** accuracy denominators.
"""

from __future__ import annotations

from docdoc.evaluation import FieldOutcomeKind, evaluate
from docdoc.evaluation.metrics import count_outcomes
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set


def _report():  # type: ignore[no-untyped-def]
    return evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())


def test_the_fixture_actually_predicts_unlabeled_fields() -> None:
    """The guard on the guard. Everything below is vacuous without this."""
    report = _report()
    unlabeled = [o for o in report.outcomes if o.kind is FieldOutcomeKind.UNLABELED]

    assert unlabeled, (
        "no UNLABELED outcome exists in the fixture, so every assertion in this "
        "file passes without exercising the case it describes"
    )
    assert {o.field_path for o in unlabeled} >= {"line_items[0].quantity"}


def test_an_unlabeled_field_is_counted_and_reported() -> None:
    """It exists in the report. Silence would be its own failure.

    A predicted field the dataset says nothing about is usually the first
    evidence that a label is missing, and a report that dropped it would make the
    dataset's gaps invisible in the one artifact that should expose them.
    """
    report = _report()

    # Eight, not nine: the document that failed at GROUND contributes none. Its
    # labelled fields are MISSING, and reporting the values its half-finished
    # extraction happened to hold would make this count a function of how far a
    # crash got before it stopped.
    assert report.metrics.counts.unlabeled == 8
    assert any(o.kind is FieldOutcomeKind.UNLABELED for o in report.outcomes)
    assert not any(
        o.kind is FieldOutcomeKind.UNLABELED and o.document_id == "failing" for o in report.outcomes
    )


def test_unlabeled_enters_no_accuracy_denominator() -> None:
    """The assertion FR-036 exists for, stated against every metric at once."""
    report = _report()
    counts = report.metrics.counts

    assert counts.labelled == counts.value_labels + counts.absence_labels
    assert counts.unlabeled not in (0,), "the fixture must have some, or this proves nothing"
    assert counts.labelled == 36, "|V| + |A|, with the nine unlabeled outside it"

    for name in ("field_accuracy", "coverage", "missing_rate", "incorrect_rate"):
        metric = report.metrics.micro[name]
        assert metric.denominator in (counts.labelled, counts.value_labels), (
            f"{name} divides by {metric.denominator}, which is neither |V| nor |V|+|A|; "
            "the unlabeled outcomes have leaked into a denominator"
        )


def test_unlabeled_is_neither_correct_nor_incorrect() -> None:
    """Not assumed right, not assumed wrong -- the two failures FR-036 names."""
    report = _report()
    counts = report.metrics.counts

    assert counts.correct == counts.correct_value + counts.correct_absence == 25
    assert counts.incorrect == 3
    # 25 + 3 + 5 + 1 + 2 == 36 == the labelled surface. The nine unlabeled
    # outcomes are in none of those five tallies.
    assert (
        counts.correct + counts.incorrect + counts.missing + counts.spurious + counts.unevaluated
        == counts.labelled
    )


def test_adding_unlabeled_predictions_moves_no_metric() -> None:
    """The property, tested directly rather than inferred from denominators.

    A model that started returning ten more unlabelled fields must produce
    identical accuracy. If this ever fails, the pipeline can improve its score by
    guessing more.
    """
    from docdoc.evaluation.outcomes import FieldOutcome
    from docdoc.evaluation.tiers import Tier

    report = _report()
    before = count_outcomes(report.outcomes, value_paths=set())

    extra = [
        FieldOutcome(
            document_id="clean",
            field_path=f"invented_{index}",
            kind=FieldOutcomeKind.UNLABELED,
            tier=Tier.PUBLIC,
            predicted="something",
        )
        for index in range(10)
    ]
    after = count_outcomes([*report.outcomes, *extra], value_paths=set())

    assert after.unlabeled == before.unlabeled + 10
    assert after.labelled == before.labelled
    assert after.correct == before.correct
    assert after.incorrect == before.incorrect
    assert after.value_labels == before.value_labels


def test_an_unlabeled_outcome_records_the_predicted_value_and_no_expected_one() -> None:
    """There is nothing expected. A blank there is the honest answer, not a gap."""
    report = _report()
    unlabeled = next(o for o in report.outcomes if o.kind is FieldOutcomeKind.UNLABELED)

    assert unlabeled.predicted is not None
    assert unlabeled.expected is None
