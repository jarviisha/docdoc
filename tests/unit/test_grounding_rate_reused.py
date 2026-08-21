"""T046 — the grounding rate is Milestone 4's, reused and never redefined (FR-033).

Two grounding rates in one system is worse than none. They diverge -- different
denominators, different treatment of correctly reported absences -- and then
every conversation about grounding has to start by establishing which number
somebody is quoting. Nothing warns you: both are plausible, both are computed
from real data, and they disagree by a few points.

So this layer aggregates ``GroundingCounts`` as Milestone 4 recorded them and
divides. It does not walk the outcomes and count grounded fields, which would be
a second definition wearing the first one's name.

``not_applicable`` stays **outside** the denominator, and that is Milestone 4's
convention rather than a choice made here: a value the model correctly reported
absent is not a grounding failure. There is nothing to ground.
"""

from __future__ import annotations

import ast
import pathlib

import docdoc.evaluation
from docdoc.evaluation import evaluate
from docdoc.grounding.result import GroundingCounts
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set

EVALUATION_DIR = pathlib.Path(docdoc.evaluation.__file__).parent


def _recorded_counts() -> GroundingCounts:
    """Sum the committed counts by hand, the way a reader would check the number."""
    predictions = prediction_set()
    exact = fuzzy = ungrounded = not_applicable = 0
    for document_id in ("clean", "near-miss", "keyed", "receipt", "failing"):
        prediction = predictions.for_document(document_id)
        if prediction is None or prediction.grounding is None:
            continue
        counts = prediction.grounding.counts
        exact += counts.exact
        fuzzy += counts.fuzzy
        ungrounded += counts.ungrounded
        not_applicable += counts.not_applicable
    return GroundingCounts(
        exact=exact, fuzzy=fuzzy, ungrounded=ungrounded, not_applicable=not_applicable
    )


def test_the_reported_rate_equals_the_one_from_the_recorded_counts() -> None:
    """The requirement, stated as an equality rather than a resemblance."""
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    recorded = _recorded_counts()
    reported = report.metrics.micro["grounding_rate"]

    assert reported.numerator == recorded.exact + recorded.fuzzy
    assert reported.denominator == recorded.exact + recorded.fuzzy + recorded.ungrounded
    assert reported.value == recorded.grounding_rate


def test_the_fixture_has_something_to_ground() -> None:
    """The guard on the guard: an all-zero fixture would satisfy any definition."""
    recorded = _recorded_counts()

    assert recorded.exact + recorded.fuzzy > 0
    assert recorded.not_applicable > 0, (
        "no correctly reported absences in the fixture, so the denominator "
        "convention below is untested"
    )


def test_not_applicable_is_outside_the_denominator() -> None:
    """A correctly reported absence is not a grounding failure (FR-008 upstream).

    Were it inside, a document full of legitimately absent fields would drag the
    grounding rate down for having correctly said nothing was there.
    """
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    recorded = _recorded_counts()
    reported = report.metrics.micro["grounding_rate"]

    assert recorded.not_applicable > 0
    assert reported.denominator == recorded.exact + recorded.fuzzy + recorded.ungrounded
    assert reported.denominator != (
        recorded.exact + recorded.fuzzy + recorded.ungrounded + recorded.not_applicable
    )


def test_the_rate_follows_the_counts_and_not_the_outcomes() -> None:
    """The property, tested by moving one of the two and watching which the rate follows.

    On this fixture the two happen to agree numerically, which is exactly what
    makes a recomputation survivable in review: the wrong implementation produces
    a believable number. So this does not compare them -- it changes the recorded
    counts while leaving every outcome's ``grounding_status`` untouched, and
    asserts the reported rate moved. A rate recomputed from outcomes could not.
    """
    predictions = prediction_set()
    clean = predictions.for_document("clean")
    assert clean is not None
    assert clean.grounding is not None

    inflated_counts = clean.grounding.counts.model_copy(
        update={"ungrounded": clean.grounding.counts.ungrounded + 7}
    )
    inflated = predictions.model_copy(
        update={
            "predictions": {
                **predictions.predictions,
                "clean": clean.model_copy(
                    update={
                        "grounding": clean.grounding.model_copy(update={"counts": inflated_counts})
                    }
                ),
            }
        }
    )

    facts = facts_for_fixtures()
    before = evaluate(golden_set(), predictions, facts=facts).metrics.micro["grounding_rate"]
    after = evaluate(golden_set(), inflated, facts=facts).metrics.micro["grounding_rate"]

    assert after.denominator == before.denominator + 7, (
        "the recorded counts moved and the reported denominator did not, so the "
        "rate is being computed from something other than Milestone 4's counts"
    )
    assert after.numerator == before.numerator


def test_no_second_grounding_rate_is_defined_anywhere_in_the_package() -> None:
    """A structural check, because the equality above only covers today's fixture.

    ``grounding_rate`` may be *named* in this package -- it is a metric key and a
    spec name -- but the arithmetic must appear once, in the one place that reads
    Milestone 4's counts.
    """
    offenders: list[str] = []
    for path in sorted(EVALUATION_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            # A division whose operands mention grounding statuses is a rate being
            # computed from outcomes rather than read from counts.
            if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
                continue
            source = ast.unparse(node)
            if "status" in source or "GroundingStatus" in source:
                offenders.append(f"{path.name}: {source}")

    assert not offenders, (
        f"a grounding rate appears to be computed from statuses rather than from "
        f"Milestone 4's recorded counts: {offenders}"
    )


def test_the_metric_definition_names_milestone_fours_terms() -> None:
    """The definition is data, and it says where the number comes from (FR-035)."""
    from docdoc.evaluation.definitions import METRICS

    spec = next(metric for metric in METRICS if metric.name == "grounding_rate")

    assert spec.numerator == "exact + fuzzy"
    assert spec.denominator == "exact + fuzzy + ungrounded"
    assert "FR-033" in spec.why
