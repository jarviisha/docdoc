"""T082 — a correction is recorded, alters nothing, and counts only once promoted.

FR-050 … FR-053, SC-019, SC-020, EVA-29.

The loop this closes: a reviewer reads a report, sees a wrong value, knows the
right one. Today that knowledge goes into a spreadsheet and the next evaluation
measures against the same stale labels.

Two properties decide whether the mechanism can be trusted, and they pull in
opposite directions:

**It must alter nothing.** A correction that edited the extraction it annotates
would make recorded pipeline output a function of who reviewed it, and every
ADR-0003 identity downstream would describe a run that never happened.

**It must eventually count.** A correction nobody can promote is a comment. So
promotion is a separate, explicit act that returns a **new** golden set with a
new identity — which makes reports either side of it visibly incomparable, rather
than silently different.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

import pytest
from pydantic import ValidationError

from docdoc.evaluation import (
    Correction,
    EvaluationError,
    Expectation,
    ExpectedLocation,
    FieldOutcomeKind,
    evaluate,
    promote,
)
from docdoc.evaluation.identity import golden_set_id_for
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set

FACTS = facts_for_fixtures()

#: The seven fields the constitution requires of a correction.
REQUIRED = (
    "field_path",
    "predicted_value",
    "corrected_value",
    "location",
    "reason",
    "annotator",
    "timestamp",
)


def _correction(report_id: str = "sha256:abc") -> Correction:
    """`near-miss`'s total: the truth is 350.00 and the run produced 300.00.

    The fixture's labels already say 350.00, so this correction agrees with them
    — which is the wrong shape for testing promotion. `_disagreeing` below is the
    one that changes an answer.
    """
    return Correction(
        report_id=report_id,
        document_id="near-miss",
        field_path="total",
        predicted_value="300.00",
        corrected_value=Decimal("350.00"),
        location=ExpectedLocation(page=0),
        reason="the thousands separator was misread",
        annotator="jh",
        timestamp=datetime(2026, 8, 20, 9, 0, 0),
    )


def _disagreeing(report_id: str = "sha256:abc") -> Correction:
    """A correction that genuinely changes the truth: the invoice total is 300.00.

    The reviewer is saying the *label* was wrong, not the prediction — which is
    the case that makes promotion observable, because the next run scores the
    same prediction against a different expectation.
    """
    return Correction(
        report_id=report_id,
        document_id="near-miss",
        field_path="total",
        predicted_value="300.00",
        corrected_value=Decimal("300.00"),
        reason="the label was wrong; the printed total is 300.00",
        annotator="jh",
        timestamp=datetime(2026, 8, 20, 9, 0, 0),
    )


# -- the model (FR-050, FR-051) ----------------------------------------------


@pytest.mark.parametrize("field", REQUIRED)
def test_a_correction_carries_every_required_field(field: str) -> None:
    """Enumerated, not counted: a rename must fail here rather than pass."""
    assert field in Correction.model_fields


def test_it_names_the_exact_run_and_result_it_corrects() -> None:
    """FR-051. Without both, it can be read as correcting a different version's output."""
    correction = _correction()

    assert correction.report_id
    assert correction.document_id


@pytest.mark.parametrize("blank", ["", "   "])
def test_a_correction_without_a_reason_is_refused(blank: str) -> None:
    """An unreviewable correction is an assertion with a name on it."""
    with pytest.raises(ValidationError, match="reason"):
        Correction(
            report_id="sha256:abc",
            document_id="near-miss",
            field_path="total",
            corrected_value=Decimal("350.00"),
            reason=blank,
            annotator="jh",
            timestamp=datetime(2026, 8, 20, 9, 0, 0),
        )


def test_a_correction_without_an_annotator_is_refused() -> None:
    with pytest.raises(ValidationError, match="annotator"):
        Correction(
            report_id="sha256:abc",
            document_id="near-miss",
            field_path="total",
            corrected_value=Decimal("350.00"),
            reason="misread",
            annotator="  ",
            timestamp=datetime(2026, 8, 20, 9, 0, 0),
        )


def test_a_correction_can_assert_an_absence() -> None:
    """Both things a label can say, so a reviewer can correct in either direction.

    Without this, "the model invented a tax id" would have no expressible fix.
    """
    correction = Correction(
        report_id="sha256:abc",
        document_id="near-miss",
        field_path="supplier.tax_id",
        predicted_value="GB-123",
        corrected_absence=True,
        reason="no tax id is printed on this invoice",
        annotator="jh",
        timestamp=datetime(2026, 8, 20, 9, 0, 0),
    )

    assert correction.as_label().expectation is Expectation.ABSENT
    assert correction.as_label().value is None


def test_a_correction_stating_nothing_is_refused() -> None:
    """Neither a value nor an asserted absence is not a correction."""
    with pytest.raises(ValidationError, match="corrected"):
        Correction(
            report_id="sha256:abc",
            document_id="near-miss",
            field_path="total",
            reason="something is wrong",
            annotator="jh",
            timestamp=datetime(2026, 8, 20, 9, 0, 0),
        )


# -- it alters nothing (FR-052, SC-019) --------------------------------------


def test_recording_a_correction_alters_zero_results_it_annotates() -> None:
    """Byte for byte, across the extraction, the grounding, and the validation."""
    predictions = prediction_set()
    near_miss = predictions.for_document("near-miss")
    assert near_miss is not None

    before = near_miss.model_dump_json()
    _correction()
    after = near_miss.model_dump_json()

    assert before == after


def test_a_correction_moves_no_metric_until_promoted() -> None:
    """SC-020. The scorer never reads corrections; there is no path for one to leak."""
    golden = golden_set()
    predictions = prediction_set()

    before = evaluate(golden, predictions, facts=FACTS)
    _disagreeing(before.report_id)
    after = evaluate(golden, predictions, facts=FACTS)

    assert before.report_id == after.report_id
    assert before.model_dump_json() == after.model_dump_json()


def test_evaluate_takes_no_corrections_argument() -> None:
    """FR-052 as a signature check.

    A ``corrections=`` parameter would be a path by which an unpromoted
    correction could move a number, and the promotion step would become optional.
    """
    import inspect

    parameters = set(inspect.signature(evaluate).parameters)
    assert not any("correct" in name for name in parameters), (
        f"evaluate() accepts {sorted(parameters)}; a corrections argument would let "
        "one move a metric without the explicit act FR-053 requires"
    )


# -- promotion (FR-053, EVA-29b) ---------------------------------------------


def test_promotion_returns_a_new_golden_set_and_does_not_mutate() -> None:
    golden = golden_set()
    snapshot = golden.model_dump_json()

    promoted = promote(golden, [_disagreeing()])

    assert promoted is not golden
    assert golden.model_dump_json() == snapshot, "promote() mutated its input"


def test_promotion_changes_the_golden_set_id() -> None:
    """Which is what makes reports either side of it visibly incomparable (FR-046)."""
    golden = golden_set()
    promoted = promote(golden, [_disagreeing()])

    assert golden_set_id_for(promoted) != golden_set_id_for(golden)


def test_the_next_run_scores_against_the_corrected_label() -> None:
    """The loop closes. This is the assertion the whole story is for."""
    golden = golden_set()
    predictions = prediction_set()

    before = evaluate(golden, predictions, facts=FACTS)
    total_before = next(
        o for o in before.outcomes if o.document_id == "near-miss" and o.field_path == "total"
    )
    assert total_before.kind is FieldOutcomeKind.INCORRECT

    after = evaluate(promote(golden, [_disagreeing()]), predictions, facts=FACTS)
    total_after = next(
        o for o in after.outcomes if o.document_id == "near-miss" and o.field_path == "total"
    )

    assert total_after.kind is FieldOutcomeKind.CORRECT
    assert after.metrics.micro["field_accuracy"].numerator == 26


def test_promotion_can_add_a_label_where_there_was_none() -> None:
    """How a reviewer closes a gap that ``UNLABELED`` exposed.

    The declared count follows, or the next load would refuse the set as a bundle
    disagreeing with its declaration -- the right check firing at the wrong moment
    and blaming the wrong person.
    """
    golden = golden_set()
    correction = Correction(
        report_id="sha256:abc",
        document_id="clean",
        field_path="line_items[0].quantity",
        predicted_value="2.0",
        corrected_value=2.0,
        reason="the quantity was never labelled",
        annotator="jh",
        timestamp=datetime(2026, 8, 20, 9, 0, 0),
    )

    promoted = promote(golden, [correction])
    document = promoted.document("clean")
    assert document is not None

    paths = {label.field_path for label in promoted.labels_for("clean")}
    assert "line_items[0].quantity" in paths
    assert document.declared_label_count == len(promoted.labels_for("clean"))


def test_a_promoted_label_keeps_its_attribution() -> None:
    """Who said so and when, carried onto the label -- and neither moves the identity.

    Attribution that changed ``golden_set_id`` would refuse comparisons over a
    typo fix in an annotator's name (EVA-6).
    """
    promoted = promote(golden_set(), [_disagreeing()])
    label = next(item for item in promoted.labels_for("near-miss") if item.field_path == "total")

    assert label.labeler == "jh"
    assert label.labeled_at == datetime(2026, 8, 20, 9, 0, 0)

    renamed = promote(
        golden_set(),
        [_disagreeing().model_copy(update={"annotator": "someone-else"})],
    )
    assert golden_set_id_for(renamed) == golden_set_id_for(promoted)


def test_promoting_a_correction_for_an_unknown_document_is_refused() -> None:
    """The two do not describe the same thing, and applying it would be an edit
    nobody asked for."""
    correction = _disagreeing().model_copy(update={"document_id": "not-in-the-set"})

    with pytest.raises(EvaluationError, match="not-in-the-set"):
        promote(golden_set(), [correction])


def test_promoting_nothing_returns_the_same_set() -> None:
    """An empty promotion must not move the identity, or a no-op would break comparison."""
    golden = golden_set()

    assert promote(golden, []) is golden


# -- what this deliberately is not (FR-054) ----------------------------------


def test_no_review_interface_queue_or_storage_is_provided() -> None:
    """Principle IX permits corrections as a model and forbids a review platform."""
    import docdoc.evaluation

    surface = set(docdoc.evaluation.__all__)
    forbidden = [
        name
        for name in surface
        if any(word in name.lower() for word in ("queue", "workflow", "assign", "store", "review"))
    ]

    assert not forbidden, f"the package exposes review-platform machinery: {forbidden}"
    assert {"Correction", "promote"} <= surface, (
        "the model and the act must both be exported, or corrections are unusable"
    )
