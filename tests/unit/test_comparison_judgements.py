"""T068 — a regression is visible and attributable (FR-045 … FR-049, SC-013 … SC-015).

The story this file tells: score one prediction set, degrade it deliberately,
score it again, and ask what the comparison says. It must name every field that
changed and no field that did not, surface the grounding fall as a **named**
regression rather than a row in a table, record which versions differed, refuse
to diff reports that do not measure the same thing, and — the one that is easiest
to get wrong — never turn an undefined metric into a delta.

The degradation is applied to the *predictions*, not to the pipeline, because the
question under test is what the comparison reports rather than whether the
pipeline can be made worse.
"""

from __future__ import annotations

import pytest

from docdoc.evaluation import (
    EvaluationError,
    EvaluationOptions,
    FieldOutcomeKind,
    Judgement,
    MetricDelta,
    compare,
    evaluate,
)
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import degraded_prediction_set, prediction_set

FACTS = facts_for_fixtures()


@pytest.fixture(scope="module")
def before():  # type: ignore[no-untyped-def]
    return evaluate(golden_set(), prediction_set(), facts=FACTS)


@pytest.fixture(scope="module")
def after():  # type: ignore[no-untyped-def]
    return evaluate(golden_set(), degraded_prediction_set(), facts=FACTS)


@pytest.fixture(scope="module")
def delta(before, after):  # type: ignore[no-untyped-def]
    return compare(before, after)


# -- what moved (FR-045, SC-014) ---------------------------------------------


def test_the_comparison_names_the_metrics_that_moved(delta) -> None:  # type: ignore[no-untyped-def]
    assert delta.metrics["field_accuracy"].judgement is Judgement.REGRESSED
    assert delta.metrics["incorrect_rate"].judgement is Judgement.REGRESSED
    assert delta.metrics["field_accuracy"].delta < 0


def test_it_names_every_changed_outcome_and_no_unchanged_one(delta) -> None:  # type: ignore[no-untyped-def]
    """SC-014: 100% of the changed outcomes in both directions, zero unchanged ones.

    The "zero unchanged" half is what makes the list readable. A comparison that
    listed every field would be a second copy of the report, and a reviewer
    would learn to skip it.
    """
    changed = {(o.document_id, o.field_path) for o in delta.changed_outcomes}

    assert changed == {("clean", "total")}
    assert len(delta.changed_outcomes) == 1


def test_it_reports_the_direction_of_each_change(delta) -> None:  # type: ignore[no-untyped-def]
    """Both directions, because a fix and a break are different news."""
    change = delta.changed_outcomes[0]

    assert change.before is FieldOutcomeKind.CORRECT
    assert change.after is FieldOutcomeKind.INCORRECT
    assert change.broke
    assert not change.fixed
    assert delta.broke == (change,)
    assert delta.fixed == ()


def test_a_fix_is_reported_as_a_fix() -> None:
    """The other direction, run as its own comparison so it cannot be assumed."""
    degraded = evaluate(golden_set(), degraded_prediction_set(), facts=FACTS)
    repaired = evaluate(golden_set(), prediction_set(), facts=FACTS)

    forward = compare(degraded, repaired)

    assert forward.fixed
    assert not forward.broke
    assert forward.metrics["field_accuracy"].judgement is Judgement.IMPROVED


def test_an_unchanged_metric_is_reported_as_unchanged(delta) -> None:  # type: ignore[no-untyped-def]
    """A zero delta is a fact, not an omission."""
    assert delta.metrics["spurious_rate"].judgement is Judgement.UNCHANGED
    assert delta.metrics["spurious_rate"].delta == 0


# -- the named grounding regression (FR-047, SC-015) -------------------------


def test_a_fall_in_grounding_rate_is_a_named_regression(delta) -> None:  # type: ignore[no-untyped-def]
    """A gate cannot read a table looking for the row that matters."""
    assert delta.grounding_regression is not None
    assert delta.grounding_regression.name == "grounding_rate"
    assert delta.grounding_regression.delta < 0
    assert delta.grounding_regression.judgement is Judgement.REGRESSED


def test_the_named_field_is_absent_when_grounding_did_not_fall(before) -> None:  # type: ignore[no-untyped-def]
    """It names a regression, not the metric. An always-present field is not a signal."""
    unchanged = compare(before, evaluate(golden_set(), prediction_set(), facts=FACTS))

    assert unchanged.grounding_regression is None
    assert unchanged.metrics["grounding_rate"].judgement is Judgement.UNCHANGED


def test_a_rise_in_a_failure_rate_is_a_regression_and_a_rise_in_accuracy_is_not(delta) -> None:  # type: ignore[no-untyped-def]
    """The sign convention, asserted rather than assumed.

    ``missing_rate`` going up is bad; ``coverage`` going up is good. Getting this
    backwards would report every improvement as a regression, and the feature
    would be switched off within a week.
    """
    assert delta.metrics["incorrect_rate"].delta > 0
    assert delta.metrics["incorrect_rate"].judgement is Judgement.REGRESSED
    assert delta.metrics["field_accuracy"].delta < 0
    assert delta.metrics["field_accuracy"].judgement is Judgement.REGRESSED


# -- attribution (FR-048) ----------------------------------------------------


def test_provenance_differences_name_what_changed(delta) -> None:  # type: ignore[no-untyped-def]
    """Without this a reader has a number that moved and a change that happened.

    Nothing connects them, and the connection gets made anyway — by whoever is
    telling the story. The degraded run is recorded under a new model version, so
    the comparison can say the fall arrived with a model change rather than
    merely alongside one.
    """
    assert "model_version" in delta.provenance_differences
    assert "prompt_hash" not in delta.provenance_differences, (
        "the prompt did not change; naming it would point a reader at the wrong "
        "cause, which is worse than naming nothing"
    )


def test_no_provenance_difference_is_reported_when_nothing_differs(before) -> None:  # type: ignore[no-untyped-def]
    """Two runs of the same thing differ in nothing, and the comparison says so.

    A field that always appears in this tuple would be noise, and a reader would
    stop looking at it -- which is the same as not having it.
    """
    same = compare(before, evaluate(golden_set(), prediction_set(), facts=FACTS))

    assert same.provenance_differences == ()


# -- refusals (FR-046, SC-013) -----------------------------------------------


def test_comparing_across_golden_sets_is_refused_naming_both(before) -> None:  # type: ignore[no-untyped-def]
    """The numbers do not measure the same thing, so their difference measures nothing."""
    golden = golden_set()
    edited_labels = (
        golden.labels["clean"][0].model_copy(update={"value": "INV-777"}),
        *golden.labels["clean"][1:],
    )
    other = golden.model_copy(update={"labels": {**golden.labels, "clean": edited_labels}})

    with pytest.raises(EvaluationError) as raised:
        compare(before, evaluate(other, prediction_set(), facts=FACTS))

    assert raised.value.expected
    assert raised.value.actual
    assert raised.value.expected != raised.value.actual
    assert raised.value.expected in str(raised.value)
    assert raised.value.actual in str(raised.value)


def test_comparing_across_metric_definitions_is_refused(before) -> None:  # type: ignore[no-untyped-def]
    """The denominators moved, so the rates are not the same quantity."""
    other = evaluate(
        golden_set(),
        prediction_set(),
        facts=FACTS,
        options=EvaluationOptions(metric_definition_version="metric_definitions@2"),
    )

    with pytest.raises(EvaluationError, match="metric_definition"):
        compare(before, other)


def test_comparing_a_partial_report_against_a_full_one_is_refused(before) -> None:  # type: ignore[no-untyped-def]
    """The smaller number is not worse, it is less (EVA-28a).

    Same dataset on both sides, so the refusal cannot be the golden-set one
    wearing a different message: what differs is only whether the restricted tier
    was included, which is exactly the case FR-046's last clause covers.
    """
    from tests.fixtures.evaluation.predictions import prediction_set as build

    full = evaluate(
        golden_set(),
        build(include_restricted=True),
        facts=FACTS,
        options=EvaluationOptions(include_restricted=True),
    )

    assert before.partial is not None, "the default fixture skips the restricted tier"
    assert full.partial is None, "including it covers the whole dataset"

    with pytest.raises(EvaluationError, match="partial"):
        compare(before, full)


def test_comparing_across_schema_identities_is_refused(before) -> None:  # type: ignore[no-untyped-def]
    golden = golden_set()
    other = evaluate(
        golden.model_copy(
            update={
                "documents": tuple(d for d in golden.documents if d.schema_identity != "receipt@1"),
                "labels": {k: v for k, v in golden.labels.items() if k != "receipt"},
            }
        ),
        prediction_set().model_copy(
            update={
                "predictions": {
                    k: v for k, v in prediction_set().predictions.items() if k != "receipt"
                }
            }
        ),
        facts=FACTS,
    )

    with pytest.raises(EvaluationError) as raised:
        compare(before, other)

    # Refused on the golden set first, which is the stronger statement: the two
    # datasets differ, so the schemas differing is a symptom.
    assert raised.value.expected != raised.value.actual


# -- None is not zero (EVA-28c) ----------------------------------------------


def test_an_undefined_to_defined_transition_is_not_reported_as_a_delta() -> None:
    """Treating ``None`` as ``0.0`` would manufacture a regression out of a new label.

    A dataset that grows an expected location goes from "no mislocation rate" to
    "a mislocation rate", and a subtraction would call that a fall from zero --
    punishing the team for labelling more.
    """
    golden = golden_set()
    stripped = {
        document_id: tuple(label.model_copy(update={"location": None}) for label in labels)
        for document_id, labels in golden.labels.items()
    }

    without = evaluate(
        golden.model_copy(update={"labels": stripped}), prediction_set(), facts=FACTS
    )
    with_locations = evaluate(golden, prediction_set(), facts=FACTS)

    # The two datasets differ, so a direct comparison is refused -- which is
    # correct, and is why the judgement is exercised on the delta itself.
    from docdoc.evaluation.compare import _judge

    became_defined = _judge(
        "mislocation_rate",
        without.metrics.micro["mislocation_rate"],
        with_locations.metrics.micro["mislocation_rate"],
    )
    became_undefined = _judge(
        "mislocation_rate",
        with_locations.metrics.micro["mislocation_rate"],
        without.metrics.micro["mislocation_rate"],
    )

    assert became_defined.judgement is Judgement.BECAME_DEFINED
    assert became_defined.delta is None
    assert became_undefined.judgement is Judgement.BECAME_UNDEFINED
    assert became_undefined.delta is None


def test_two_undefined_sides_are_unchanged_rather_than_a_zero_delta() -> None:
    """Nothing was asked before and nothing is asked now. That is not an improvement."""
    from docdoc.evaluation.compare import _judge
    from docdoc.evaluation.metrics import MetricValue

    undefined = MetricValue(name="mislocation_rate", value=None, numerator=0, denominator=0)
    judged = _judge("mislocation_rate", undefined, undefined)

    assert judged.judgement is Judgement.UNCHANGED
    assert judged.delta is None


# -- it decides nothing (FR-049) ---------------------------------------------


def test_the_comparison_states_and_does_not_decide(delta) -> None:  # type: ignore[no-untyped-def]
    """No pass, no fail, no threshold, no exit code.

    Whether a build fails is policy configured on top of this output. A
    comparison that also decided would bury the decision inside the thing being
    measured.
    """
    rendered = delta.model_dump_json()

    for forbidden in ("should_fail", "blocking", "passed", "threshold", "exit_code", "verdict"):
        assert forbidden not in rendered, f"the comparison decided something: {forbidden!r}"

    assert set(MetricDelta.model_fields) == {
        "name",
        "before",
        "after",
        "delta",
        "judgement",
    }


def test_the_regressions_list_is_a_statement_a_gate_can_read(delta) -> None:  # type: ignore[no-untyped-def]
    """Policy reads this. It does not read a decision, because there is not one."""
    assert "field_accuracy" in delta.regressions
    assert "grounding_rate" in delta.regressions
    assert isinstance(delta.regressions, tuple)


def test_the_comparison_names_both_reports(delta, before, after) -> None:  # type: ignore[no-untyped-def]
    """A delta detached from the two runs it describes is not attributable."""
    assert delta.before_report_id == before.report_id
    assert delta.after_report_id == after.report_id
    assert delta.before_report_id != delta.after_report_id
