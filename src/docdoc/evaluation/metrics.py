"""Counting the outcomes, and turning counts into metrics that state their terms.

Every metric reports its numerator and denominator (FR-029), a zero denominator
yields ``None`` and never ``0.0`` (FR-032), and where micro and macro can differ
both are reported and both are labelled (FR-031).

**A macro-average also reports how many documents it averaged over**, and that is
not bookkeeping. A document whose per-document metric is undefined cannot enter a
mean, so excluding it silently makes the macro number describe an unstated subset
— FR-015's failure mode at a different scale, and invisible unless the count
travels with the number (EVA-18a).
"""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

# Runtime imports: these appear in pydantic field annotations.
from docdoc.evaluation.alignment import GroupOutcome
from docdoc.evaluation.definitions import METRIC_DEFINITION_VERSION
from docdoc.evaluation.location import LocationAgreement
from docdoc.evaluation.outcomes import FieldOutcomeKind
from docdoc.evaluation.predictions import Stage

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from docdoc.evaluation.outcomes import FieldOutcome
    from docdoc.grounding.result import GroundingCounts
    from docdoc.validation.result import Verdict

__all__ = [
    "Averaging",
    "DatasetMetrics",
    "DocumentScore",
    "MetricValue",
    "OutcomeCounts",
    "count_outcomes",
    "dataset_metrics",
    "document_score",
]


class Averaging(StrEnum):
    MICRO = "micro"
    MACRO = "macro"


class MetricValue(BaseModel):
    """One number, with the terms it came from (EVA-18)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str

    #: ``None`` when the denominator is zero. **Never ``0.0``** -- a rate of zero
    #: reads as total failure, and there was no question asked (FR-032).
    value: float | None
    numerator: int
    denominator: int
    averaging: Averaging = Averaging.MICRO

    #: Macro only. How many documents entered the mean, and how many were left
    #: out because their own metric was undefined (EVA-18a).
    documents_averaged: int | None = None
    documents_undefined: int | None = None

    @property
    def defined(self) -> bool:
        return self.value is not None


def _rate(
    name: str,
    numerator: int,
    denominator: int,
    averaging: Averaging = Averaging.MICRO,
) -> MetricValue:
    return MetricValue(
        name=name,
        value=None if denominator == 0 else numerator / denominator,
        numerator=numerator,
        denominator=denominator,
        averaging=averaging,
    )


class OutcomeCounts(BaseModel):
    """The tallies every metric divides (EVA-17)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Value labels (V) resolve to exactly one of these four.
    correct_value: int = 0
    incorrect: int = 0
    missing: int = 0
    unevaluated_value: int = 0

    #: Absence labels (A) resolve to exactly one of these three.
    correct_absence: int = 0
    spurious: int = 0
    unevaluated_absence: int = 0

    #: In neither partition, and in no accuracy denominator (FR-036).
    unlabeled: int = 0

    #: Location agreement, a separate axis from the outcome above (EVA-14).
    agrees: int = 0
    disagrees: int = 0
    not_assessable: int = 0

    @property
    def value_labels(self) -> int:
        """|V|."""
        return self.correct_value + self.incorrect + self.missing + self.unevaluated_value

    @property
    def absence_labels(self) -> int:
        """|A|."""
        return self.correct_absence + self.spurious + self.unevaluated_absence

    @property
    def labelled(self) -> int:
        """|V| + |A|."""
        return self.value_labels + self.absence_labels

    @property
    def correct(self) -> int:
        return self.correct_value + self.correct_absence

    @property
    def unevaluated(self) -> int:
        return self.unevaluated_value + self.unevaluated_absence


def count_outcomes(
    outcomes: Iterable[FieldOutcome], *, value_paths: set[tuple[str, str]]
) -> OutcomeCounts:
    """Tally outcomes into the partitions EVA-17 divides.

    ``value_paths`` holds the ``(document_id, field_path)`` pairs whose label
    states a value, so that ``CORRECT`` and ``UNEVALUATED`` can be attributed to
    the right partition -- both occur in V and in A, with different denominators.
    """
    fields: dict[str, int] = {}
    for outcome in outcomes:
        key = (outcome.document_id, outcome.field_path)
        is_value = key in value_paths
        match outcome.kind:
            case FieldOutcomeKind.CORRECT:
                name = "correct_value" if is_value else "correct_absence"
            case FieldOutcomeKind.UNEVALUATED:
                name = "unevaluated_value" if is_value else "unevaluated_absence"
            case FieldOutcomeKind.INCORRECT:
                name = "incorrect"
            case FieldOutcomeKind.MISSING:
                name = "missing"
            case FieldOutcomeKind.SPURIOUS:
                name = "spurious"
            case FieldOutcomeKind.UNLABELED:
                name = "unlabeled"
        fields[name] = fields.get(name, 0) + 1

        match outcome.location_agreement:
            case LocationAgreement.AGREES:
                fields["agrees"] = fields.get("agrees", 0) + 1
            case LocationAgreement.DISAGREES:
                fields["disagrees"] = fields.get("disagrees", 0) + 1
            case LocationAgreement.NOT_ASSESSABLE:
                fields["not_assessable"] = fields.get("not_assessable", 0) + 1
            case None:
                pass

    return OutcomeCounts(**fields)


def _metrics_from(
    counts: OutcomeCounts,
    grounding: GroundingCounts | None,
    averaging: Averaging = Averaging.MICRO,
) -> dict[str, MetricValue]:
    exact = grounding.exact if grounding else 0
    fuzzy = grounding.fuzzy if grounding else 0
    ungrounded = grounding.ungrounded if grounding else 0

    return {
        "field_accuracy": _rate("field_accuracy", counts.correct, counts.labelled, averaging),
        "coverage": _rate(
            "coverage", counts.correct_value + counts.incorrect, counts.value_labels, averaging
        ),
        "missing_rate": _rate("missing_rate", counts.missing, counts.value_labels, averaging),
        "incorrect_rate": _rate("incorrect_rate", counts.incorrect, counts.value_labels, averaging),
        # Milestone 4's definition, from its recorded counts. `not_applicable` is
        # outside the denominator because a correctly reported absence is not a
        # grounding failure -- this feature defines no second rate (FR-033).
        "grounding_rate": _rate(
            "grounding_rate", exact + fuzzy, exact + fuzzy + ungrounded, averaging
        ),
        "spurious_rate": _rate("spurious_rate", counts.spurious, counts.absence_labels, averaging),
        "unevaluated_rate": _rate(
            "unevaluated_rate", counts.unevaluated, counts.labelled, averaging
        ),
        "mislocation_rate": _rate(
            "mislocation_rate", counts.disagrees, counts.agrees + counts.disagrees, averaging
        ),
    }


class DocumentScore(BaseModel):
    """One document's outcomes and metrics, including whether it processed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    counts: OutcomeCounts
    metrics: dict[str, MetricValue]
    evaluated: bool
    failed_stage: Stage | None = None


def document_score(
    *,
    document_id: str,
    outcomes: Sequence[FieldOutcome],
    value_paths: set[tuple[str, str]],
    grounding: GroundingCounts | None,
    evaluated: bool,
    failed_stage: Stage | None,
) -> DocumentScore:
    counts = count_outcomes(outcomes, value_paths=value_paths)
    return DocumentScore(
        document_id=document_id,
        counts=counts,
        metrics=_metrics_from(counts, grounding),
        evaluated=evaluated,
        failed_stage=failed_stage,
    )


class DatasetMetrics(BaseModel):
    """Dataset-level metrics, both averagings, plus the per-field breakdown."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    metric_definition_version: str = METRIC_DEFINITION_VERSION
    counts: OutcomeCounts

    #: Pool every outcome, then divide. One field, one vote.
    micro: dict[str, MetricValue]

    #: Mean of the per-document metrics. One document, one vote. Each carries
    #: `documents_averaged` and `documents_undefined` (EVA-18a).
    macro: dict[str, MetricValue]

    #: Per field path across the dataset. A single dataset number is not
    #: sufficient evidence under Principle IX (FR-030).
    per_field_path: dict[str, dict[str, MetricValue]]

    group_outcomes: tuple[GroupOutcome, ...] = ()

    #: Milestone 5's verdicts, reused not recomputed (FR-034), so that "the
    #: extraction was wrong" and "validation caught it" stay two visible facts.
    validation_verdicts: dict[str, int] = {}


def _macro(
    document_scores: Sequence[DocumentScore], names: Iterable[str]
) -> dict[str, MetricValue]:
    macro: dict[str, MetricValue] = {}
    for name in names:
        defined = [score.metrics[name] for score in document_scores if score.metrics[name].defined]
        undefined = len(document_scores) - len(defined)
        if not defined:
            macro[name] = MetricValue(
                name=name,
                value=None,
                numerator=0,
                denominator=0,
                averaging=Averaging.MACRO,
                documents_averaged=0,
                documents_undefined=undefined,
            )
            continue
        total = sum(metric.value or 0.0 for metric in defined)
        macro[name] = MetricValue(
            name=name,
            value=total / len(defined),
            numerator=len(defined),
            denominator=len(defined),
            averaging=Averaging.MACRO,
            documents_averaged=len(defined),
            documents_undefined=undefined,
        )
    return macro


def dataset_metrics(
    *,
    outcomes: Sequence[FieldOutcome],
    document_scores: Sequence[DocumentScore],
    value_paths: set[tuple[str, str]],
    grounding: GroundingCounts | None,
    group_outcomes: Sequence[GroupOutcome] = (),
    verdicts: dict[Verdict, int] | None = None,
) -> DatasetMetrics:
    counts = count_outcomes(outcomes, value_paths=value_paths)
    micro = _metrics_from(counts, grounding)

    by_path: dict[str, list[FieldOutcome]] = {}
    for outcome in outcomes:
        by_path.setdefault(outcome.field_path, []).append(outcome)

    per_field_path = {
        path: _metrics_from(count_outcomes(items, value_paths=value_paths), None)
        for path, items in sorted(by_path.items())
    }

    return DatasetMetrics(
        counts=counts,
        micro=micro,
        macro=_macro(document_scores, micro.keys()),
        per_field_path=per_field_path,
        group_outcomes=tuple(group_outcomes),
        validation_verdicts={str(k): v for k, v in sorted((verdicts or {}).items())},
    )
