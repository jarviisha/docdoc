"""T087 — a change detector for the scorer's output, not a breakage detector.

``SCORER_VERSION`` moves whenever scoring output moves for fixed inputs. Nothing
detects that on its own: a comparator change, a denominator change, an ordering
change all produce a different report from the same dataset, and the version
sitting in ``identity.py`` does not notice.

So this file pins the version and the ``report_id`` of the committed tier. **When
it fails, that is the check working.** Clearing it is deliberate and takes one of
two forms, and choosing between them is a human judgement stated in the commit
message:

- **The output changed on purpose.** Bump ``SCORER_VERSION`` and refresh the
  snapshot below. The bump is what makes every existing report visibly
  incomparable with every new one (FR-046), which is correct, because they were
  computed differently.
- **The output changed by accident.** Fix the code. The snapshot was right.

This is the same review obligation ADR-0008 places on schema hashes and ADR-0003
on processor versions: no system can classify a semantic change, so the
classification is made by a person and recorded where reviewers read it.
"""

from __future__ import annotations

import pytest

from docdoc.evaluation import SCORER_ID, SCORER_VERSION, evaluate
from tests.fixtures.evaluation.datasets import (
    committed_golden_set,
    committed_prediction_set,
    facts_for_fixtures,
)

#: Bumped when scoring output moves for fixed inputs, with the classification
#: stated in the commit message.
EXPECTED_SCORER_VERSION = "1.0.0"

#: The committed public tier's report, under the default options. Refreshed in
#: the same commit as a deliberate version bump, and never on its own -- a
#: snapshot refreshed without a bump is the accident this file exists to catch,
#: silenced.
EXPECTED_REPORT_ID = "sha256:c3b4bc8e686aaa25a710067857b6aff7dc90f20c679057cba229b992d88d2b8e"

#: The numbers a reader would quote. Pinned beside the id because an id is opaque:
#: when this fails, these say *what* moved, which is the first thing anyone asks.
EXPECTED_METRICS = {
    "field_accuracy": (26, 28),
    "coverage": (24, 25),
    "missing_rate": (1, 25),
    "incorrect_rate": (1, 25),
    "grounding_rate": (30, 30),
}


def _report():  # type: ignore[no-untyped-def]
    return evaluate(committed_golden_set(), committed_prediction_set(), facts=facts_for_fixtures())


def test_the_scorer_version_is_pinned() -> None:
    """A bump must be a deliberate edit here as well as in ``identity.py``."""
    assert SCORER_VERSION == EXPECTED_SCORER_VERSION, (
        f"SCORER_VERSION is {SCORER_VERSION!r} and this snapshot expects "
        f"{EXPECTED_SCORER_VERSION!r}. If the bump was deliberate, refresh this "
        "constant and EXPECTED_REPORT_ID in the same commit, and state in the "
        "message whether output changed and how"
    )


def test_the_scorer_id_is_stable() -> None:
    """The id names the processor and does not move; the *version* is what moves."""
    assert SCORER_ID == "golden-set-scorer"


def test_the_committed_tiers_report_id_has_not_moved() -> None:
    """The detector.

    A failure here means one of: the dataset changed, the committed predictions
    changed, the scorer's output changed, or the default options changed. All
    four are real events that should be visible in review, and none of them
    announce themselves anywhere else.
    """
    report = _report()

    assert report.report_id == EXPECTED_REPORT_ID, (
        f"the committed tier now scores as {report.report_id}. This is a change "
        "detector, not a breakage detector: if the change was intended, bump "
        "SCORER_VERSION and refresh this snapshot, stating the classification in "
        "the commit message (EVA-24, ADR-0003)"
    )


@pytest.mark.parametrize(("name", "expected"), sorted(EXPECTED_METRICS.items()))
def test_the_headline_metrics_have_not_moved(name: str, expected: tuple[int, int]) -> None:
    """What moved, in readable terms, so a failed snapshot is diagnosable."""
    metric = _report().metrics.micro[name]

    assert (metric.numerator, metric.denominator) == expected


def test_the_snapshot_covers_the_versions_that_can_move_a_number() -> None:
    """The report records every versioned rule, so the snapshot transitively pins them.

    A comparator, alignment policy, location rule, or metric definition that
    changed would move ``options_hash`` and therefore ``report_id`` -- which is
    why one pinned id is enough and four more constants would be duplication.
    """
    provenance = _report().provenance

    assert provenance.metric_definition_version == "metric_definitions@1"
    assert provenance.entry_alignment_version == "positional@1"
    assert provenance.location_rule_version == "page_box@1"
    assert set(provenance.comparator_versions.values()) == {"exact@1"}


def test_the_detector_would_actually_fire() -> None:
    """The guard on the guard.

    A snapshot compared against a value derived from the same run passes for any
    output at all. This confirms the pinned id is a literal that a different run
    genuinely fails against.
    """
    from docdoc.evaluation import EvaluationOptions

    other = evaluate(
        committed_golden_set(),
        committed_prediction_set(),
        facts=facts_for_fixtures(),
        options=EvaluationOptions(metric_definition_version="metric_definitions@2"),
    )

    assert other.report_id != EXPECTED_REPORT_ID
