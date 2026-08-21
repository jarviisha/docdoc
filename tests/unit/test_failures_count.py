"""T045 — failing a document lowers the score (FR-037, FR-005, SC-006).

**The single most important test in this feature**, and the reason is that the
failure it guards against is invisible in every individual number.

The arithmetic: a pipeline that crashes on its hardest documents and drops them
from the denominator scores *higher* than one that attempts them and gets some
wrong. Nothing in the report looks wrong. Accuracy went up. Coverage went up.
The missing rate went down. Every metric moved in the direction a team celebrates,
and the cause was a regression severe enough to crash.

That is not a hypothetical failure mode — it is the default one. Dropping a
document that raised is the obvious implementation, it reads as robustness, and
a reviewer would have to think about denominators to catch it.

So both cases stay in every denominator, and they stay *distinct*:

- **``UNEVALUATED``** — no prediction for this document at all (FR-005).
- **``MISSING``** — a prediction exists and records that the document failed
  part-way; its labelled fields are missing values (FR-037).

Both are counted. Only one is a defect. A report that merged them could not tell
"we never ran this" from "we ran it and it broke".
"""

from __future__ import annotations

import pytest

from docdoc.evaluation import FieldOutcomeKind, Stage, evaluate
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set


def _without(golden, document_id: str):  # type: ignore[no-untyped-def]
    """The same dataset with one document deleted — the shrunk denominator."""
    return golden.model_copy(
        update={
            "documents": tuple(d for d in golden.documents if d.document_id != document_id),
            "labels": {k: v for k, v in golden.labels.items() if k != document_id},
        }
    )


def test_a_failing_document_scores_lower_than_the_same_dataset_without_it() -> None:
    """The assertion the whole file exists for.

    Identical pipeline, identical predictions for every document that worked. The
    only difference is whether the document that failed is honestly counted or
    quietly dropped. If these ever come out equal, a crash has become free.
    """
    facts = facts_for_fixtures()
    golden = golden_set()

    honest = evaluate(golden, prediction_set(include_failing=True), facts=facts)
    laundered = evaluate(
        _without(golden, "failing"),
        prediction_set(include_failing=False),
        facts=facts,
    )

    assert honest.metrics.micro["field_accuracy"].value is not None
    assert laundered.metrics.micro["field_accuracy"].value is not None
    assert honest.metrics.micro["field_accuracy"].value < (
        laundered.metrics.micro["field_accuracy"].value
    ), (
        "dropping the document that failed did not raise the score, which means "
        "the failing document is not in the denominator -- or the fixture's "
        "failing document is not hard enough to matter"
    )


def test_the_shrunk_denominator_is_the_whole_mechanism() -> None:
    """Named explicitly, so the previous test cannot pass for a different reason.

    The numerator is unchanged -- the failing document contributed nothing correct
    either way. Only the denominator moved, and that is exactly the trick.
    """
    facts = facts_for_fixtures()
    golden = golden_set()

    honest = evaluate(golden, prediction_set(include_failing=True), facts=facts).metrics.micro[
        "field_accuracy"
    ]
    laundered = evaluate(
        _without(golden, "failing"), prediction_set(include_failing=False), facts=facts
    ).metrics.micro["field_accuracy"]

    assert honest.numerator == laundered.numerator == 25
    assert honest.denominator == 36
    assert laundered.denominator == 32
    assert honest.denominator - laundered.denominator == 4, "the four labels of `failing`"


def test_a_failed_documents_labelled_fields_count_as_missing() -> None:
    """FR-037. Not excluded, not unevaluated -- missing, which is a defect."""
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    outcomes = [o for o in report.outcomes if o.document_id == "failing"]

    assert len(outcomes) == 4
    assert {o.kind for o in outcomes} == {FieldOutcomeKind.MISSING}


def test_a_failed_document_is_counted_as_a_processing_failure() -> None:
    """The stage it stopped at is recorded, so the report can say what broke."""
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    score = next(s for s in report.document_scores if s.document_id == "failing")

    assert score.failed_stage is Stage.GROUND
    assert score.evaluated, "a failed document was still evaluated; it just did not finish"


def test_a_document_with_no_prediction_is_unevaluated_and_still_counted() -> None:
    """FR-005, the other half. Absent is not the same as broken, and both count."""
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    outcomes = [o for o in report.outcomes if o.document_id == "silent"]
    score = next(s for s in report.document_scores if s.document_id == "silent")

    assert {o.kind for o in outcomes} == {FieldOutcomeKind.UNEVALUATED}
    assert not score.evaluated
    assert score.failed_stage is None, "nothing failed; nothing ran"
    assert score.counts.labelled == 2, "still in the denominator"


def test_unevaluated_and_missing_are_never_merged() -> None:
    """Two facts with two fixes. Merging them makes the report unactionable."""
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    counts = report.metrics.counts

    assert counts.missing == 5
    assert counts.unevaluated == 2
    assert counts.missing != counts.unevaluated


@pytest.mark.parametrize("metric", ["field_accuracy", "coverage"])
def test_dropping_a_silent_document_would_also_raise_the_score(metric: str) -> None:
    """FR-005 gets the same test as FR-037, because it is the same arithmetic.

    A document nobody produced a prediction for is the cheapest possible way to
    shrink a denominator: no crash, no error, nothing in a log.
    """
    facts = facts_for_fixtures()
    golden = golden_set()
    predictions = prediction_set()

    honest = evaluate(golden, predictions, facts=facts).metrics.micro[metric]
    laundered = evaluate(_without(golden, "silent"), predictions, facts=facts).metrics.micro[metric]

    assert honest.value < laundered.value
    assert honest.denominator > laundered.denominator
