"""T043 — every metric against a hand-computed literal (SC-003).

**The expected values below are written as literals, not derived by the code
under test.** That is the entire design of this file. A test that recomputed the
expected value with the same function it was testing would pass on any consistent
mistake -- a swapped denominator, an off-by-one in a partition, a metric that
divides by the wrong thing everywhere -- and would report green while every
number in the repository was wrong in the same direction.

So the arithmetic is done here, in the docstrings, by hand, from the fixture:

``clean`` (11 labels)
    9 value labels, all correct; 2 absence labels, both correctly absent.
``near-miss`` (6 labels)
    3 correct; ``total`` wrong (350.00 expected, 300.00 predicted) -> INCORRECT;
    ``supplier.legal_name`` predicted absent -> MISSING; ``supplier.tax_id``
    labelled absent and predicted present -> SPURIOUS.
``keyed`` (9 labels)
    7 correct; the two ``description`` fields mismatch positionally -> INCORRECT.
``receipt`` (4 labels)
    all 4 correct. The second schema, so nothing here is invoice-shaped.
``failing`` (4 labels)
    stopped at GROUND -> all 4 MISSING, and in every denominator (FR-037).
``silent`` (2 labels)
    no prediction at all -> both UNEVALUATED, and in every denominator (FR-005).

Totals: 36 labelled fields, of which 33 state a value (|V|) and 3 assert an
absence (|A|).

    correct         = 11 + 3 + 7 + 4 = 25   (23 value + 2 absence)
    incorrect       =  0 + 1 + 2 + 0 =  3
    missing         =  0 + 1 + 0 + 4 =  5
    spurious        =  0 + 1 + 0 + 0 =  1
    unevaluated     =  2
    unlabeled       =  9  (predicted, never labelled, in no denominator)

    field_accuracy  = 25 / 36
    coverage        = (23 correct_value + 3 incorrect) / 33 = 26 / 33
    missing_rate    =  5 / 33
    incorrect_rate  =  3 / 33
    spurious_rate   =  1 /  3
    unevaluated_rate=  2 / 36
"""

from __future__ import annotations

import pytest

from docdoc.evaluation import Averaging, evaluate
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set

#: (metric, numerator, denominator) at dataset level, computed by hand above.
DATASET_MICRO = (
    ("field_accuracy", 25, 36),
    ("coverage", 26, 33),
    ("missing_rate", 5, 33),
    ("incorrect_rate", 3, 33),
    ("spurious_rate", 1, 3),
    ("unevaluated_rate", 2, 36),
)

#: (document, correct, labelled) per document, from the same hand count.
DOCUMENT_ACCURACY = (
    ("clean", 11, 11),
    ("near-miss", 3, 6),
    ("keyed", 7, 9),
    ("receipt", 4, 4),
    ("failing", 0, 4),
    ("silent", 0, 2),
)

#: A field path appears once per document that labels it. ``total`` is labelled by
#: five invoices and one receipt: clean ✓, near-miss ✗, keyed ✓, receipt ✓,
#: failing (missing), silent (unevaluated) -> 3 correct of 6.
FIELD_PATH_ACCURACY = (
    ("total", 3, 6),
    ("invoice_number", 3, 5),
    ("supplier.tax_id", 1, 2),
    ("merchant_name", 1, 1),
)


@pytest.fixture(scope="module")
def report():  # type: ignore[no-untyped-def]
    return evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())


# -- dataset level -----------------------------------------------------------


@pytest.mark.parametrize(("name", "numerator", "denominator"), DATASET_MICRO)
def test_the_dataset_metric_matches_the_hand_computed_value(
    report, name: str, numerator: int, denominator: int
) -> None:  # type: ignore[no-untyped-def]
    metric = report.metrics.micro[name]

    assert (metric.numerator, metric.denominator) == (numerator, denominator), (
        f"{name} reported {metric.numerator}/{metric.denominator}; the fixture was "
        f"counted by hand as {numerator}/{denominator}"
    )
    assert metric.value == pytest.approx(numerator / denominator)


def test_the_grounding_rate_comes_from_milestone_fours_counts(report) -> None:  # type: ignore[no-untyped-def]
    """The fifth constitutional metric, and the one this feature must not redefine.

    Its numerator is not hand-counted here because it is not this feature's to
    count: FR-033 says the definition and the recorded counts are Milestone 4's.
    What is asserted is that the reported number *is* those counts -- which is
    the actual requirement, and a stronger one than a literal would be.
    """
    grounding = report.metrics.micro["grounding_rate"]
    predictions = prediction_set()

    exact = fuzzy = ungrounded = 0
    for document_id in ("clean", "near-miss", "keyed", "receipt", "failing"):
        prediction = predictions.for_document(document_id)
        if prediction is None or prediction.grounding is None:
            continue
        exact += prediction.grounding.counts.exact
        fuzzy += prediction.grounding.counts.fuzzy
        ungrounded += prediction.grounding.counts.ungrounded

    assert grounding.numerator == exact + fuzzy
    assert grounding.denominator == exact + fuzzy + ungrounded


def test_every_metric_states_its_numerator_and_denominator(report) -> None:  # type: ignore[no-untyped-def]
    """FR-029. A rate without its terms cannot be checked by the person reading it."""
    for name, metric in report.metrics.micro.items():
        assert metric.name == name
        assert metric.denominator >= 0
        assert metric.numerator >= 0
        if metric.denominator:
            assert metric.value == pytest.approx(metric.numerator / metric.denominator)


# -- document level ----------------------------------------------------------


@pytest.mark.parametrize(("document_id", "correct", "labelled"), DOCUMENT_ACCURACY)
def test_the_per_document_metric_matches_the_hand_computed_value(
    report, document_id: str, correct: int, labelled: int
) -> None:  # type: ignore[no-untyped-def]
    score = next(s for s in report.document_scores if s.document_id == document_id)
    accuracy = score.metrics["field_accuracy"]

    assert (accuracy.numerator, accuracy.denominator) == (correct, labelled)


def test_the_document_scores_sum_to_the_dataset_totals(report) -> None:  # type: ignore[no-untyped-def]
    """One document, one contribution. Nothing is counted twice or lost."""
    total_correct = sum(s.counts.correct for s in report.document_scores)
    total_labelled = sum(s.counts.labelled for s in report.document_scores)

    assert total_correct == report.metrics.counts.correct == 25
    assert total_labelled == report.metrics.counts.labelled == 36


# -- field-path level --------------------------------------------------------


@pytest.mark.parametrize(("field_path", "correct", "labelled"), FIELD_PATH_ACCURACY)
def test_the_per_field_path_metric_matches_the_hand_computed_value(
    report, field_path: str, correct: int, labelled: int
) -> None:  # type: ignore[no-untyped-def]
    """FR-030. A single dataset number is not sufficient evidence under Principle IX.

    Per-field-path is where "accuracy is 0.94" becomes "and every one of the
    failures is the same field", which is the difference between a number and a
    thing somebody can act on.
    """
    metrics = report.metrics.per_field_path[field_path]
    accuracy = metrics["field_accuracy"]

    assert (accuracy.numerator, accuracy.denominator) == (correct, labelled)


def test_every_labelled_field_path_appears_in_the_breakdown(report) -> None:  # type: ignore[no-untyped-def]
    labelled = {o.field_path for o in report.outcomes}

    assert labelled == set(report.metrics.per_field_path)


# -- micro and macro ---------------------------------------------------------


def test_micro_and_macro_are_both_reported_and_both_labelled(report) -> None:  # type: ignore[no-untyped-def]
    """FR-031. They differ here, which is what makes reporting both necessary."""
    micro = report.metrics.micro["field_accuracy"]
    macro = report.metrics.macro["field_accuracy"]

    assert micro.averaging is Averaging.MICRO
    assert macro.averaging is Averaging.MACRO
    assert micro.value != pytest.approx(macro.value), (
        "micro and macro coincide on this fixture, so it cannot show that the two "
        "are computed differently"
    )


def test_the_macro_average_is_the_mean_of_the_per_document_values(report) -> None:  # type: ignore[no-untyped-def]
    """Hand-checked from the six per-document accuracies above.

    (11/11 + 3/6 + 7/9 + 4/4 + 0/4 + 0/2) / 6
    = (1 + 0.5 + 0.777… + 1 + 0 + 0) / 6
    """
    expected = (1 + 0.5 + 7 / 9 + 1 + 0 + 0) / 6
    macro = report.metrics.macro["field_accuracy"]

    assert macro.value == pytest.approx(expected)
    assert macro.documents_averaged == 6
    assert macro.documents_undefined == 0


def test_a_macro_average_states_how_many_documents_it_left_out(report) -> None:  # type: ignore[no-untyped-def]
    """EVA-18a. A document whose own metric is undefined cannot enter a mean.

    Excluding it silently makes the macro number describe an unstated subset --
    FR-015's failure at a different scale, and invisible unless the count travels
    with the number. ``spurious_rate`` is the case here: only two of the six
    documents label an absence at all.
    """
    spurious = report.metrics.macro["spurious_rate"]

    assert spurious.documents_averaged == 2
    assert spurious.documents_undefined == 4
    assert spurious.documents_averaged + spurious.documents_undefined == len(report.document_scores)


# -- Milestone 5's counts, reused not recomputed (T094, FR-034) --------------
#
# The verdict distribution and the validation counts answer different questions,
# and a report needs both. The distribution says how many documents came out
# `invalid`; only the counts say how many checks ran, how many passed, and how
# many could not be evaluated at all. Without them a document that failed one
# check and a document where nothing could be checked look identical in the
# report -- which is exactly the distinction Milestone 5 added a third verdict to
# preserve.


def test_the_report_carries_the_verdict_distribution(report) -> None:  # type: ignore[no-untyped-def]
    """Hand-counted: `clean`, `keyed`, and `receipt` validate; `near-miss` does not."""
    assert report.metrics.validation_verdicts == {"invalid": 1, "valid": 3}


def test_the_report_carries_milestone_fives_counts(report) -> None:  # type: ignore[no-untyped-def]
    """Summed from the recorded results, and asserted against them rather than a literal.

    A literal here would pin Milestone 5's rule set, so adding a constraint to a
    fixture schema would fail this file for a reason that has nothing to do with
    evaluation. What must hold is that the report's counts *are* the recorded
    ones.
    """
    predictions = prediction_set()
    expected = dict.fromkeys(("declared", "evaluated", "passed", "failed", "not_evaluated"), 0)
    for document_id in ("clean", "near-miss", "keyed", "receipt", "failing"):
        prediction = predictions.for_document(document_id)
        if prediction is None or prediction.validation is None:
            continue
        for name in expected:
            expected[name] += getattr(prediction.validation.counts, name)

    counts = report.metrics.validation_counts
    assert counts is not None
    for name, total in expected.items():
        assert getattr(counts, name) == total, name


def test_the_summed_counts_still_reconcile(report) -> None:  # type: ignore[no-untyped-def]
    """Milestone 5's own invariant survives summation, and is re-checked by the model.

    ``declared == passed + failed + not_evaluated`` is linear, so it holds for a
    sum of documents exactly as it holds for one. Asserted rather than assumed,
    because a summation that dropped a field would produce a `ValidationCounts`
    that reconciles by coincidence on a fixture where the field is zero.
    """
    counts = report.metrics.validation_counts
    assert counts is not None

    assert counts.declared == counts.passed + counts.failed + counts.not_evaluated
    assert counts.evaluated == counts.passed + counts.failed
    assert counts.declared > 0, "a fixture with nothing declared would prove nothing"


def test_the_counts_and_the_verdicts_say_different_things(report) -> None:  # type: ignore[no-untyped-def]
    """The reason FR-034 asks for both.

    One document is `invalid`, and it is invalid because of a *subset* of its
    checks. A reader with only the verdict cannot tell whether one check failed or
    all of them did.
    """
    counts = report.metrics.validation_counts
    assert counts is not None

    assert report.metrics.validation_verdicts["invalid"] == 1
    assert counts.failed >= 1
    assert counts.passed > counts.failed, (
        "the invalid document failed only some of its checks, which is the fact "
        "the verdict alone cannot carry"
    )


def test_a_run_with_no_validation_reports_none_rather_than_zeros() -> None:
    """A zeroed `ValidationCounts` reconciles perfectly and reads as "all passed".

    That is the opposite of what an absence means -- the same reason FR-032
    refuses to report an empty denominator as `0.0`.
    """
    from docdoc.evaluation import DocumentPrediction, PredictionSet, evaluate

    golden = golden_set()
    stripped = PredictionSet(
        predictions={
            document_id: DocumentPrediction(
                document_id=document_id,
                extraction=prediction.extraction,
                grounding=prediction.grounding,
                parser_id=prediction.parser_id,
                parser_version=prediction.parser_version,
            )
            for document_id, prediction in prediction_set().predictions.items()
        }
    )

    report = evaluate(golden, stripped, facts=facts_for_fixtures())

    assert report.metrics.validation_counts is None
    assert report.metrics.validation_verdicts == {}
