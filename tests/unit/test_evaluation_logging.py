"""T056 — one event per run, and no document content in any of them (FR-057, FR-061, SC-017).

The boundary Milestones 3, 4, and 5 set holds here, and this stage is the one
most able to blur it:

- **Outcomes carry values by design.** "expected 350.00, predicted 300.00" is
  what makes a near-miss diagnosable, and FR-026 requires it.
- **Logs carry identities, versions, counts, and hashes.** Nothing else.

"Values never appear anywhere" is the wrong reading of FR-057 and would make
outcomes useless. The rule is about where they appear: a report is an artifact
somebody chose to produce and can control the disclosure of; a log line goes to
whatever aggregator the deployment happens to ship to.

**Refusals log too**, and that is the half most likely to be missing. A refused
run has no report and therefore no ``report_id``, so the event carries the two
identities that explain the refusal instead. For this stage that is the whole
diagnosis of its most common failure -- a schema identity that does not match the
labels -- and a refusal that logs nothing leaves the operator with an exception
in someone else's terminal.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest

from docdoc.evaluation import EvaluationError, EvaluationOptions, evaluate
from docdoc.evaluation.observe import EVENT_NAME
from tests.fixtures.evaluation.datasets import DOCUMENT_TEXT, facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import (
    mismatched_prediction,
    prediction_for,
    prediction_set,
)


def _events(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    return [
        record.docdoc  # type: ignore[attr-defined]
        for record in caplog.records
        if record.name == "docdoc.evaluation" and hasattr(record, "docdoc")
    ]


@pytest.fixture
def logged(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger="docdoc.evaluation")
    return caplog


def test_exactly_one_event_per_run(logged: pytest.LogCaptureFixture) -> None:
    """One run, one event. Two would double every count on a dashboard."""
    evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())

    events = _events(logged)
    assert len(events) == 1
    assert events[0]["event"] == EVENT_NAME
    assert events[0]["outcome"] == "ok"


def test_the_event_carries_identities_versions_counts_and_duration(
    logged: pytest.LogCaptureFixture,
) -> None:
    evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    event = _events(logged)[0]

    for key in (
        "report_id",
        "golden_set_id",
        "prediction_set_id",
        "scorer_id",
        "scorer_version",
        "metric_definition_version",
        "entry_alignment_version",
        "location_rule_version",
        "documents",
        "labelled_fields",
        "correct",
        "incorrect",
        "missing",
        "spurious",
        "unlabeled",
        "unevaluated",
        "duration_ms",
    ):
        assert key in event, f"the run event does not carry {key!r}"

    assert event["labelled_fields"] == 36
    assert event["correct"] == 25
    assert isinstance(event["duration_ms"], float)


def test_the_event_carries_the_partial_flag(logged: pytest.LogCaptureFixture) -> None:
    """Next to the metrics deliberately.

    A dashboard reading accuracy without it would report a healthy system that
    had measured a fraction of the dataset and never said so.
    """
    evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    event = _events(logged)[0]

    assert event["partial"] is True, "the fixture skips the restricted tier"
    assert event["covered_labels"] == 36
    assert event["declared_labels"] == 40


@pytest.mark.parametrize(
    ("name", "build"),
    [
        ("unknown document", "unknown"),
        ("schema identity", "identity"),
        ("schema hash", "hash"),
    ],
)
def test_each_refusal_emits_an_event_naming_both_sides(
    logged: pytest.LogCaptureFixture, name: str, build: str
) -> None:
    """A refusal that logs nothing is the failure this stage will hit most and explain least."""
    golden = golden_set()
    predictions = prediction_set()

    if build == "unknown":
        predictions = predictions.model_copy(
            update={"predictions": {**predictions.predictions, "ghost": prediction_for("clean")}}
        )
    elif build == "identity":
        predictions = predictions.model_copy(
            update={"predictions": {**predictions.predictions, "clean": mismatched_prediction()}}
        )
    else:
        golden = golden.model_copy(
            update={
                "documents": tuple(
                    d.model_copy(update={"schema_hash": "sha256:" + "0" * 64})
                    if d.document_id == "clean"
                    else d
                    for d in golden.documents
                )
            }
        )

    with pytest.raises(EvaluationError):
        evaluate(golden, predictions, facts=facts_for_fixtures())

    events = _events(logged)
    assert len(events) == 1, f"{name}: expected exactly one refusal event"
    event = events[0]
    assert event["outcome"] == "refused"
    assert event["golden_set_id"], "a refused run still knows which dataset it refused"
    assert event["prediction_set_id"], "and which prediction set"
    assert event["reason"]


def test_a_refusal_event_carries_no_report_id(logged: pytest.LogCaptureFixture) -> None:
    """There is no report. Inventing a field for one would be a lie with a schema."""
    predictions = prediction_set()
    predictions = predictions.model_copy(
        update={"predictions": {**predictions.predictions, "ghost": prediction_for("clean")}}
    )

    with pytest.raises(EvaluationError):
        evaluate(golden_set(), predictions, facts=facts_for_fixtures())

    assert "report_id" not in _events(logged)[0]


# -- the disclosure boundary -------------------------------------------------


def _forbidden_strings() -> set[str]:
    """Every label value and every document word in the fixture.

    Swept over the whole golden set rather than sampled: a leak of one field is
    the same defect as a leak of all of them, and which field leaks depends on
    which branch a run happened to take.
    """
    values: set[str] = set()
    for labels in golden_set().labels.values():
        for label in labels:
            if label.value is not None:
                values.add(str(label.value))
    for text in DOCUMENT_TEXT.values():
        values |= {word for word in text.split() if len(word) > 4}
    return {value for value in values if len(value) > 4}


def test_no_document_text_or_field_value_appears_in_any_log_line(
    logged: pytest.LogCaptureFixture,
) -> None:
    """FR-057, swept over the whole golden set (SC-017)."""
    evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())

    rendered = "\n".join(str(event) for event in _events(logged))
    leaked = sorted(value for value in _forbidden_strings() if value in rendered)

    assert not leaked, f"these values from the dataset appear in log output: {leaked}"


def test_the_sweep_would_notice_a_leak() -> None:
    """The guard on the guard. An empty forbidden set finds nothing, forever."""
    forbidden = _forbidden_strings()

    assert len(forbidden) > 10, f"the sweep only knows {len(forbidden)} strings to look for"
    assert "ACME LTD" in forbidden or "1240.00" in forbidden
    assert any(value in "a line mentioning 1240.00 by accident" for value in forbidden)


def test_no_restricted_tier_value_appears_in_any_log_line(
    logged: pytest.LogCaptureFixture,
) -> None:
    """US3/AC5 pairs the two surfaces: "when the report is **written or logged**".

    Every other sweep in this file runs the public tier, because that is what the
    default fixture scores. The restricted tier is the one the acceptance
    criterion actually names, and it was asserted nowhere.

    **The code is structurally safe**, and that is an argument for asserting it
    cheaply rather than for skipping it: `log_evaluation` builds its payload from
    identities, versions, counts, and `redacted_tiers`, so today there is no field
    a value could travel in. The day somebody adds one, this is the test that
    should fail — and the report's own outcomes are already redacted to hashes, so
    a leak here would be the *only* place a restricted value escaped.
    """
    report = evaluate(
        golden_set(),
        prediction_set(include_restricted=True),
        facts=facts_for_fixtures(),
        options=EvaluationOptions(include_restricted=True),
    )

    assert report.redacted_tiers, "the run must actually cover the restricted tier"

    rendered = "\n".join(str(event) for event in _events(logged))
    leaked = sorted(value for value in _forbidden_strings() if value in rendered)
    assert not leaked, f"these values reach the log on a restricted-tier run: {leaked}"

    # And the event still says what happened, so "no leak" was not achieved by
    # logging nothing at all.
    event = _events(logged)[0]
    assert event["outcome"] == "ok"
    assert event["redacted_tiers"] == ["restricted"]


def test_a_restricted_run_logs_which_tiers_it_redacted(
    logged: pytest.LogCaptureFixture,
) -> None:
    """A report that redacted silently is indistinguishable from one with nothing
    to hide, and the same is true of its log line (FR-056)."""
    evaluate(
        golden_set(),
        prediction_set(include_restricted=True),
        facts=facts_for_fixtures(),
        options=EvaluationOptions(include_restricted=True),
    )

    assert _events(logged)[0]["redacted_tiers"] == ["restricted"]


def test_outcomes_carry_the_values_the_logs_do_not(logged: pytest.LogCaptureFixture) -> None:
    """The other half of the boundary, asserted so it cannot be satisfied by silence.

    A scorer that stripped values everywhere would pass the leak test above and
    make every near-miss undiagnosable.
    """
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())

    near_miss = next(
        o for o in report.outcomes if o.document_id == "near-miss" and o.field_path == "total"
    )
    assert near_miss.expected == "350.00"
    assert near_miss.predicted == "300.00"

    rendered = "\n".join(str(event) for event in _events(logged))
    assert "350.00" not in rendered
