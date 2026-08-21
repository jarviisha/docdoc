"""T061 — exact and fuzzy grounding scores are never pooled (FR-039, ADR-0004, ADR-0005).

An exact score is ``1.0`` **by definition** -- assigned structurally when a value
resolves verbatim. A fuzzy score is a **measured** similarity. They are numbers
on different scales that happen to share a range, and a mean over both is a
quantity with no meaning: it moves when the ratio of exact to fuzzy matches
changes, which is a fact about the documents rather than about quality.

ADR-0004 records the incomparability, and Milestone 4 puts the warning in the
field's own ``description`` so it travels into the generated schema. This layer is
where the temptation is strongest, because averaging is what this layer does. So
scores surface **per outcome** -- attached to the one comparison they describe --
and the aggregate that exists is the **grounding rate**, which counts outcomes by
status and never averages their scores.
"""

from __future__ import annotations

import ast
import pathlib

import pytest

import docdoc.evaluation
from docdoc.evaluation import evaluate
from docdoc.grounding.result import GroundingStatus
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set

EVALUATION_DIR = pathlib.Path(docdoc.evaluation.__file__).parent


def test_scores_surface_per_outcome() -> None:
    """Attached to the one comparison each describes, which is the only honest place."""
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    scored = [o for o in report.outcomes if o.grounding_score is not None]

    assert scored, "the fixture grounds something; no outcome carries a score"
    for outcome in scored:
        assert outcome.grounding_status is not None, (
            f"{outcome.field_path} carries a score with no status, so a reader "
            "cannot tell whether 1.0 was measured or assigned"
        )


def test_no_aggregate_score_appears_anywhere_in_a_report() -> None:
    """The report has no ``mean_grounding_score``, and must not grow one."""
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())

    rendered = report.model_dump_json()
    for forbidden in ("mean_score", "average_score", "avg_score", "mean_grounding"):
        assert forbidden not in rendered, f"the report carries {forbidden!r}"

    metric_names = set(report.metrics.micro)
    assert not any("score" in name for name in metric_names), (
        f"a metric named after a score exists: {sorted(metric_names)}. The aggregate "
        "this layer reports is the grounding *rate*, which counts statuses"
    )


def test_the_grounding_rate_counts_statuses_rather_than_averaging_scores() -> None:
    """The distinction, made concrete.

    Changing a fuzzy score without changing its status must move nothing. A rate
    computed from statuses cannot see it; a mean over scores would.
    """
    predictions = prediction_set()
    clean = predictions.for_document("clean")
    assert clean is not None
    assert clean.grounding is not None

    fuzzy_like = {
        path: outcome.model_copy(update={"score": 0.51})
        if outcome.status is not GroundingStatus.UNGROUNDED
        else outcome
        for path, outcome in clean.grounding.outcomes.items()
    }
    rescored = predictions.model_copy(
        update={
            "predictions": {
                **predictions.predictions,
                "clean": clean.model_copy(
                    update={
                        "grounding": clean.grounding.model_copy(update={"outcomes": fuzzy_like})
                    }
                ),
            }
        }
    )

    facts = facts_for_fixtures()
    before = evaluate(golden_set(), predictions, facts=facts).metrics.micro["grounding_rate"]
    after = evaluate(golden_set(), rescored, facts=facts).metrics.micro["grounding_rate"]

    assert (before.numerator, before.denominator) == (after.numerator, after.denominator), (
        "the grounding rate moved when only the scores did, so it is averaging "
        "them rather than counting statuses"
    )


def _modules() -> list[pathlib.Path]:
    return sorted(EVALUATION_DIR.rglob("*.py"))


@pytest.mark.parametrize("path", _modules(), ids=lambda p: p.name)
def test_no_module_sums_or_averages_a_grounding_score(path: pathlib.Path) -> None:
    """The structural check, because the behavioural one only covers today's shapes.

    Looks for ``sum``/``mean``/``statistics`` applied to something naming a score.
    A pooled score is easy to add and impossible to spot in a number.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if name not in {"sum", "mean", "fmean", "statistics.mean", "max", "min"}:
            continue
        rendered = ast.unparse(node)
        if "grounding_score" in rendered or "score" in rendered.split("(", 1)[-1]:
            offenders.append(rendered)

    assert not offenders, (
        f"{path.name} pools grounding scores: {offenders}. An exact score is 1.0 by "
        "definition and a fuzzy score is measured, so an aggregate over both is a "
        "number with no meaning (ADR-0004)"
    )


def test_scores_stay_separable_by_tier_in_the_report() -> None:
    """Where scores surface, the status that gives them meaning surfaces with them.

    That is what "per outcome and per tier" means in practice: a consumer that
    wants an exact-only summary can compute one; a consumer that wants a blended
    one has to write the mistake themselves rather than read it here.
    """
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())

    by_status: dict[str, list[float]] = {}
    for outcome in report.outcomes:
        if outcome.grounding_score is None or outcome.grounding_status is None:
            continue
        by_status.setdefault(str(outcome.grounding_status), []).append(outcome.grounding_score)

    assert by_status, "no scored outcomes at all"
    for status, scores in by_status.items():
        if status == "exact":
            assert set(scores) == {1.0}, "an exact score is 1.0 by definition"
