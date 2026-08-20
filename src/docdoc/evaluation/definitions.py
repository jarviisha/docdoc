"""`metric_definitions@1` — the numerators and denominators, as data.

Principle IX names five metrics and does not define them. This module is where
they are defined, and they are **data rather than formulas embedded in the
scorer** for a specific reason: the denominators are the part a future contributor
will want to "improve" when a number looks unflattering, and putting them behind a
version turns that edit into a visible, comparison-breaking act instead of a step
in a chart nobody can explain (FR-035).

Labels partition into **V** (stating a value) and **A** (asserting a correct
absence). ``unlabeled`` is in neither.

============== ================================== ==================
Metric         Numerator                          Denominator
============== ================================== ==================
field_accuracy correct (value + absence)          |V| + |A|
coverage       correct_value + incorrect          |V|
missing_rate   missing                            |V|
incorrect_rate incorrect                          |V|
grounding_rate exact + fuzzy                      exact+fuzzy+ungrounded
============== ================================== ==================

Two decisions inside that table are worth stating rather than inferring.

**``unevaluated`` is in every denominator.** FR-005 says a golden-set document
with no prediction must not be removed from any denominator, and FR-037 says the
same for a document that failed to process. Both exist to stop the same
arithmetic: dropping the documents you crashed on raises your score. A crash
therefore lowers accuracy, lowers coverage, and shows up in its own rate.

**``unlabeled`` is in none of them.** Including it would make accuracy a function
of how completely the dataset happens to be labelled rather than of how well the
pipeline performed (FR-036).

**Coverage was the one genuinely open choice.** Adopted: answer rate over value
labels — of the fields the truth says hold a value, for how many did the run
produce one, right or wrong. Rejected: evaluation completeness, the fraction of
labelled fields that received any outcome, because that is already carried more
precisely by the partial declaration and would read 1.0 on every healthy run.

**This version also pins the comparator versions, the entry-alignment version,
and the location rule with its threshold** (EVA-22a). Each of them can move a
numerator, so a change to any is a bump here, and FR-046's refusal then covers it
without a second mechanism.
"""

from __future__ import annotations

from typing import Final

__all__ = ["METRICS", "METRIC_DEFINITION_VERSION", "MetricSpec"]

METRIC_DEFINITION_VERSION: Final = "metric_definitions@1"


class MetricSpec:
    """One metric's name and the counts it divides."""

    __slots__ = ("denominator", "name", "numerator", "why")

    def __init__(self, name: str, numerator: str, denominator: str, why: str) -> None:
        self.name = name
        self.numerator = numerator
        self.denominator = denominator
        self.why = why

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"MetricSpec({self.name!r}, {self.numerator}/{self.denominator})"


#: The five the constitution requires, plus the three this milestone reports
#: alongside them. Order is the order they appear in a report.
METRICS: Final[tuple[MetricSpec, ...]] = (
    MetricSpec(
        "field_accuracy",
        "correct",
        "|V| + |A|",
        "How often the run agreed with the truth, over everything the truth speaks about.",
    ),
    MetricSpec(
        "coverage",
        "correct_value + incorrect",
        "|V|",
        "How often the run produced a value where the truth says one exists, right or wrong.",
    ),
    MetricSpec(
        "missing_rate",
        "missing",
        "|V|",
        "How often it produced nothing where the truth says something is there.",
    ),
    MetricSpec(
        "incorrect_rate",
        "incorrect",
        "|V|",
        "How often it produced the wrong value rather than none.",
    ),
    MetricSpec(
        "grounding_rate",
        "exact + fuzzy",
        "exact + fuzzy + ungrounded",
        "Milestone 4's definition, reused from its recorded counts. Correctly "
        "reported absences stay outside the denominator (FR-033).",
    ),
    MetricSpec(
        "spurious_rate",
        "spurious",
        "|A|",
        "How often it invented a value the truth says should be absent.",
    ),
    MetricSpec(
        "unevaluated_rate",
        "unevaluated",
        "|V| + |A|",
        "How much of the labelled surface received no prediction at all.",
    ),
    MetricSpec(
        "mislocation_rate",
        "disagrees",
        "agrees + disagrees",
        "Of the values whose location could be assessed, how often it was "
        "grounded somewhere the label does not name. Not-assessable outcomes "
        "leave this denominator (EVA-14c).",
    ),
)
