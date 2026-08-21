"""Comparing two reports: what moved, by how much, and what changed underneath.

A number that fell and a change that happened are two facts. Without something
connecting them, a reader has a coincidence and a conclusion is one confident
sentence away. So this states the movement **and** what differed between the two
runs, and it decides nothing about either.

Four decisions in here are worth reading before the code.

**It refuses rather than diffing incomparable reports** (FR-046). Two reports
scored against different datasets, different schemas, or different metric
definitions produce numbers that do not mean the same thing, and subtracting them
produces a delta that means nothing at all -- while looking exactly like a real
one. A partial report against a full one is the same failure with a friendlier
face: the smaller number is not worse, it is *less*.

**The grounding regression is its own field, not one row in a table** (FR-047).
The constitution's fourth quality gate treats a fall in grounding rate as
blocking, and a gate cannot read a table looking for the row that matters. If it
had to, the gate would be written by hand in CI by each team that wanted it, and
would be written differently each time.

**``None`` is not zero** (EVA-28c). Where a metric is undefined on one side, the
judgement is ``became_defined`` or ``became_undefined`` -- never a subtraction.
Treating ``None`` as ``0.0`` would manufacture a regression out of a dataset that
grew a label, which is the one thing a team should be rewarded for.

**It states what moved and decides nothing** (FR-049). Whether a build fails is
policy configured on top of this output. A comparison that also decided would
bury the decision inside the thing being measured, and the next person asking
"why did this fail?" would have to read the scorer to find out.
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from docdoc.evaluation.errors import EvaluationError, naming
from docdoc.evaluation.metrics import MetricValue
from docdoc.evaluation.outcomes import FieldOutcomeKind
from docdoc.evaluation.tiers import Tier

if TYPE_CHECKING:
    from docdoc.evaluation.report import EvaluationReport

__all__ = [
    "ChangedOutcome",
    "Comparison",
    "Judgement",
    "MetricDelta",
    "compare",
]

#: Metrics where a **fall** is the regression. Everything else is a rate of
#: failure, where a rise is. Kept as data rather than as a sign convention buried
#: in an ``if``, because "did this get better?" is the question the whole feature
#: exists to answer and it must not depend on remembering which way each metric
#: points.
_HIGHER_IS_BETTER = frozenset({"field_accuracy", "coverage", "grounding_rate"})


class Judgement(StrEnum):
    """What happened to one metric. Five values, and two of them are not deltas."""

    IMPROVED = "improved"
    UNCHANGED = "unchanged"
    REGRESSED = "regressed"

    #: The metric had no denominator before and has one now. Not an improvement
    #: and not a regression -- the question was not being asked, and now it is.
    BECAME_DEFINED = "became_defined"

    #: And the reverse, which is usually a dataset that lost labels rather than a
    #: pipeline that got worse. Reported as itself so nobody reads it as either.
    BECAME_UNDEFINED = "became_undefined"


class MetricDelta(BaseModel):
    """One metric's before, after, movement, and what that movement means."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    before: MetricValue | None
    after: MetricValue | None

    #: ``None`` whenever either side is undefined. There is no number to
    #: subtract, and inventing one is exactly what EVA-28c forbids.
    delta: float | None
    judgement: Judgement


class ChangedOutcome(BaseModel):
    """One field whose outcome differs between the two runs (EVA-28)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    field_path: str
    tier: Tier
    before: FieldOutcomeKind | None
    after: FieldOutcomeKind | None

    @property
    def broke(self) -> bool:
        """Was correct, is not. The list a reviewer reads first."""
        return self.before is FieldOutcomeKind.CORRECT and self.after is not (
            FieldOutcomeKind.CORRECT
        )

    @property
    def fixed(self) -> bool:
        return self.after is FieldOutcomeKind.CORRECT and self.before is not (
            FieldOutcomeKind.CORRECT
        )


class Comparison(BaseModel):
    """What moved between two reports, and what differed underneath them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    before_report_id: str
    after_report_id: str

    metrics: dict[str, MetricDelta]

    #: **Named, not one row among many** (FR-047). The constitution's fourth
    #: quality gate blocks on a fall in grounding rate, and a gate cannot read a
    #: table.
    grounding_regression: MetricDelta | None = None

    #: Every field whose outcome changed, in both directions, in the report's
    #: total order.
    changed_outcomes: tuple[ChangedOutcome, ...] = ()

    #: Which of the recorded versions differ. Without this a reader has a number
    #: that moved and a change that happened and no evidence connecting them,
    #: which is how a coincidence becomes a conclusion (FR-048).
    provenance_differences: tuple[str, ...] = ()

    @property
    def regressions(self) -> tuple[str, ...]:
        """Every metric that got worse. Still a statement, still not a decision."""
        return tuple(
            name
            for name, delta in sorted(self.metrics.items())
            if delta.judgement is Judgement.REGRESSED
        )

    @property
    def broke(self) -> tuple[ChangedOutcome, ...]:
        return tuple(outcome for outcome in self.changed_outcomes if outcome.broke)

    @property
    def fixed(self) -> tuple[ChangedOutcome, ...]:
        return tuple(outcome for outcome in self.changed_outcomes if outcome.fixed)


#: The provenance fields worth diffing: each one can explain a moved number.
#: ``repo_revision`` is deliberately absent -- it differs on almost every pair and
#: explains nothing, so including it would bury the fields that do.
_DIFFED = (
    "model_ids",
    "model_versions",
    "prompt_hashes",
    "parser_ids",
    "parser_versions",
    "grounding_versions",
    "validator_versions",
    "scorer_version",
    "schema_identities",
    "schema_hashes",
)

#: Reported under the names FR-048 uses, which are singular where the provenance
#: field is a tuple over the documents scored.
_DIFF_NAMES = {
    "model_ids": "model_id",
    "model_versions": "model_version",
    "prompt_hashes": "prompt_hash",
    "parser_ids": "parser_id",
    "parser_versions": "parser_version",
    "grounding_versions": "grounding_version",
    "validator_versions": "validator_version",
    "scorer_version": "scorer_version",
    "schema_identities": "schema_identity",
    "schema_hashes": "schema_hash",
}


def _refuse_if_incomparable(before: EvaluationReport, after: EvaluationReport) -> None:
    """Naming both sides, every time (FR-046, SC-013)."""
    first = before.provenance
    second = after.provenance

    if first.golden_set_id != second.golden_set_id:
        raise EvaluationError(
            "cannot compare reports scored against different golden sets: "
            f"{first.golden_set_id} and {second.golden_set_id}. The numbers do not "
            "measure the same thing, so their difference measures nothing",
            expected=first.golden_set_id,
            actual=second.golden_set_id,
        )

    for name in ("schema_identities", "schema_hashes"):
        left = getattr(first, name)
        right = getattr(second, name)
        if left != right:
            raise EvaluationError(
                f"cannot compare reports whose {name} differ: {list(left)} and "
                f"{list(right)}. A label written under one schema version says "
                "nothing about a result produced under another (ADR-0008)",
                field_path=name,
                expected=str(list(left)),
                actual=str(list(right)),
            )

    if first.metric_definition_version != second.metric_definition_version:
        raise EvaluationError(
            "cannot compare reports computed under different metric definitions: "
            f"{first.metric_definition_version} and {second.metric_definition_version}. "
            "The denominators moved, so the rates are not the same quantity",
            field_path="metric_definition_version",
            expected=first.metric_definition_version,
            actual=second.metric_definition_version,
        )

    if (before.partial is None) != (after.partial is None):
        partial, full = ("before", "after") if before.partial is not None else ("after", "before")
        raise EvaluationError(
            f"cannot compare a partial report against a full one: {partial} covered "
            f"part of the dataset and {full} covered all of it. The smaller number "
            "is not worse, it is less",
            expected="partial" if before.partial is not None else "complete",
            actual="partial" if after.partial is not None else "complete",
        )


def _judge(name: str, before: MetricValue | None, after: MetricValue | None) -> MetricDelta:
    """One metric's movement, with ``None`` handled as itself rather than as zero."""
    before_value = before.value if before else None
    after_value = after.value if after else None

    if before_value is None and after_value is None:
        judgement = Judgement.UNCHANGED
        delta = None
    elif before_value is None:
        judgement = Judgement.BECAME_DEFINED
        delta = None
    elif after_value is None:
        judgement = Judgement.BECAME_UNDEFINED
        delta = None
    else:
        delta = after_value - before_value
        if delta == 0:
            judgement = Judgement.UNCHANGED
        elif (delta > 0) is (name in _HIGHER_IS_BETTER):
            judgement = Judgement.IMPROVED
        else:
            judgement = Judgement.REGRESSED

    return MetricDelta(name=name, before=before, after=after, delta=delta, judgement=judgement)


def _changed_outcomes(
    before: EvaluationReport, after: EvaluationReport
) -> tuple[ChangedOutcome, ...]:
    """Every field whose outcome differs, in both directions and in the total order."""
    left = {(o.document_id, o.field_path): o for o in before.outcomes}
    right = {(o.document_id, o.field_path): o for o in after.outcomes}

    changed: list[ChangedOutcome] = []
    for key in sorted(set(left) | set(right)):
        was = left.get(key)
        now = right.get(key)
        if was is not None and now is not None and was.kind is now.kind:
            continue
        reference = now or was
        assert reference is not None
        changed.append(
            ChangedOutcome(
                document_id=key[0],
                field_path=key[1],
                tier=reference.tier,
                before=None if was is None else was.kind,
                after=None if now is None else now.kind,
            )
        )
    return tuple(changed)


def _provenance_differences(before: EvaluationReport, after: EvaluationReport) -> tuple[str, ...]:
    return tuple(
        _DIFF_NAMES[name]
        for name in _DIFFED
        if getattr(before.provenance, name) != getattr(after.provenance, name)
    )


def compare(before: EvaluationReport, after: EvaluationReport) -> Comparison:
    """State what moved between two reports. Decide nothing about it (FR-049).

    Raises:
        EvaluationError: the two reports are not comparable -- a different golden
            set, a different schema identity or hash, a different metric
            definition version, or one partial and one complete (FR-046).
    """
    with naming(before.provenance.golden_set_id or None):
        _refuse_if_incomparable(before, after)

    names = sorted(set(before.metrics.micro) | set(after.metrics.micro))
    metrics = {
        name: _judge(name, before.metrics.micro.get(name), after.metrics.micro.get(name))
        for name in names
    }

    grounding = metrics.get("grounding_rate")
    return Comparison(
        before_report_id=before.report_id,
        after_report_id=after.report_id,
        metrics=metrics,
        grounding_regression=(
            grounding if grounding and grounding.judgement is Judgement.REGRESSED else None
        ),
        changed_outcomes=_changed_outcomes(before, after),
        provenance_differences=_provenance_differences(before, after),
    )
