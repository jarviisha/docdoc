"""T059 — ``report_id`` moves on what can change a number, and on nothing else (FR-042, SC-012).

**Both directions, because an identity that moves on everything is as useless as
one that moves on nothing.**

Too sensitive, and every report is incomparable with every other: ``compare()``
refuses across dataset identities (FR-046), so an id that shifted when somebody
fixed a typo in an annotator's name would break regression detection for a change
that cannot move a metric. The team's response would be to stop comparing.

Too insensitive, and two genuinely different measurements share an id: a report
scored under a changed comparator, or a changed denominator, would claim to be
the same measurement as one scored before. Comparison would silently diff numbers
that do not mean the same thing, which is the failure FR-046 exists to prevent --
arriving through the identity rather than through the check.

So the ``MOVES`` and ``DOES_NOT_MOVE`` tables below are the requirement, stated as
two lists that must both be non-empty.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from docdoc.evaluation import EvaluationOptions, evaluate
from docdoc.evaluation.identity import golden_set_id_for, prediction_set_id_for
from tests.fixtures.evaluation.datasets import (
    facts_for_fixtures,
    golden_set,
    keyed_golden_set,
)
from tests.fixtures.evaluation.predictions import prediction_set


def _score(golden=None, predictions=None, options=None, repo_revision="abc"):  # type: ignore[no-untyped-def]
    return evaluate(
        golden or golden_set(),
        predictions or prediction_set(),
        facts=facts_for_fixtures(),
        options=options,
        repo_revision=repo_revision,
    )


@pytest.fixture(scope="module")
def baseline_id() -> str:
    return _score().report_id


# -- it MUST move ------------------------------------------------------------


def test_it_moves_when_the_scorer_version_changes(
    baseline_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scorer whose output moved for fixed inputs is a different measurement."""
    import docdoc.evaluation.identity as identity

    monkeypatch.setattr(identity, "SCORER_VERSION", "9.9.9")

    assert _score().report_id != baseline_id


def test_it_moves_when_a_comparator_version_changes(baseline_id: str) -> None:
    """Leniency is a declared, versioned change -- and it changes numerators."""
    options = EvaluationOptions(
        comparator_versions={**EvaluationOptions().comparator_versions, "string": "casefold@1"}
    )

    assert _score(options=options).report_id != baseline_id


def test_it_moves_when_the_metric_definition_version_changes(baseline_id: str) -> None:
    """The denominators are behind this version, which is the point of FR-035."""
    options = EvaluationOptions(metric_definition_version="metric_definitions@2")

    assert _score(options=options).report_id != baseline_id


def test_it_moves_when_the_entry_alignment_version_changes(baseline_id: str) -> None:
    assert _score(options=EvaluationOptions(entry_alignment_version="keyed@1")).report_id != (
        baseline_id
    )


def test_it_moves_when_the_location_rule_changes(baseline_id: str) -> None:
    """``page_box@2`` would mean a different containment threshold (EVA-22a)."""
    assert _score(options=EvaluationOptions(location_rule_version="page_box@2")).report_id != (
        baseline_id
    )


def test_it_moves_when_a_label_value_changes(baseline_id: str) -> None:
    """The dataset is what is being measured against; changing it changes the measurement."""
    golden = golden_set()
    labels = golden.labels["clean"]
    edited = (labels[0].model_copy(update={"value": "INV-999"}), *labels[1:])

    assert (
        _score(
            golden=golden.model_copy(update={"labels": {**golden.labels, "clean": edited}})
        ).report_id
        != baseline_id
    )


def test_it_moves_when_a_document_joins_the_dataset(baseline_id: str) -> None:
    """A smaller dataset is a different measurement, and its id must say so.

    Removed from *both* sides: leaving the prediction behind would be refused
    under FR-005 rather than scored, which would test the refusal instead of the
    identity.
    """
    golden = golden_set()
    trimmed = golden.model_copy(
        update={
            "documents": tuple(d for d in golden.documents if d.document_id != "receipt"),
            "labels": {k: v for k, v in golden.labels.items() if k != "receipt"},
        }
    )
    predictions = prediction_set()
    without = predictions.model_copy(
        update={"predictions": {k: v for k, v in predictions.predictions.items() if k != "receipt"}}
    )

    assert _score(golden=trimmed, predictions=without).report_id != baseline_id


def test_it_moves_when_an_alignment_key_is_declared(baseline_id: str) -> None:
    """The case that proves the dataset's own declarations are inside the identity.

    Declaring a key changes which entries are compared, which changes numerators
    -- so two reports either side of it must not claim to be the same measurement.
    """
    assert _score(golden=keyed_golden_set()).report_id != baseline_id


def test_it_moves_when_a_prediction_changes(baseline_id: str) -> None:
    """Through the validation artifact id, which ADR-0003 makes transitive."""
    predictions = prediction_set()
    clean = predictions.for_document("clean")
    assert clean is not None
    assert clean.validation is not None

    tampered = predictions.model_copy(
        update={
            "predictions": {
                **predictions.predictions,
                "clean": clean.model_copy(
                    update={
                        "validation": clean.validation.model_copy(
                            update={"artifact_id": "sha256:" + "f" * 64}
                        )
                    }
                ),
            }
        }
    )

    assert _score(predictions=tampered).report_id != baseline_id


def test_it_moves_when_the_restricted_tier_is_included(baseline_id: str) -> None:
    """A run over more documents is a different measurement, not a bigger one."""
    assert _score(options=EvaluationOptions(include_restricted=True)).report_id != baseline_id


# -- it MUST NOT move --------------------------------------------------------


def test_it_does_not_move_when_a_labeler_changes(baseline_id: str) -> None:
    """FR-019 records who stated a label; it cannot change a metric (EVA-6).

    An identity that shifted here would refuse every comparison across a typo fix
    in an annotator's name -- and the team would respond by not comparing.
    """
    golden = golden_set()
    relabelled = {
        document_id: tuple(label.model_copy(update={"labeler": "someone-else"}) for label in labels)
        for document_id, labels in golden.labels.items()
    }

    assert _score(golden=golden.model_copy(update={"labels": relabelled})).report_id == baseline_id


def test_it_does_not_move_when_a_labeled_at_changes(baseline_id: str) -> None:
    golden = golden_set()
    restamped = {
        document_id: tuple(
            label.model_copy(update={"labeled_at": datetime(2001, 1, 1)}) for label in labels
        )
        for document_id, labels in golden.labels.items()
    }

    assert _score(golden=golden.model_copy(update={"labels": restamped})).report_id == baseline_id


def test_it_does_not_move_when_the_repo_revision_changes(baseline_id: str) -> None:
    """Recorded in provenance, outside the identity.

    Two checkouts of the same code must produce comparable reports, or every
    branch would be incomparable with main.
    """
    assert _score(repo_revision="deadbeef").report_id == baseline_id


def test_it_does_not_move_when_the_documents_are_reordered(baseline_id: str) -> None:
    golden = golden_set()
    reordered = golden.model_copy(update={"documents": tuple(reversed(golden.documents))})

    assert _score(golden=reordered).report_id == baseline_id


def test_the_dataset_identity_ignores_annotation_metadata() -> None:
    """Asserted at the level below too, so the insensitivity is not accidental.

    ``report_id`` folds ``golden_set_id``; if the ignoring happened only at the
    outer layer, a future formula change would silently start moving it.
    """
    golden = golden_set()
    annotated = {
        document_id: tuple(
            label.model_copy(update={"labeler": "x", "labeled_at": datetime(1999, 9, 9)})
            for label in labels
        )
        for document_id, labels in golden.labels.items()
    }

    assert golden_set_id_for(golden) == golden_set_id_for(
        golden.model_copy(update={"labels": annotated})
    )


def test_the_prediction_identity_moves_on_the_recorder(baseline_id: str) -> None:
    """EVA-10: a prediction set recorded by a different recorder is different data.

    A script would have no version to record here, which is why recording is a
    package rather than a file under ``examples/``.
    """
    predictions = prediction_set()
    other = predictions.model_copy(update={"recorder_version": "2.0.0"})

    assert prediction_set_id_for(predictions) != prediction_set_id_for(other)
    assert _score(predictions=other).report_id != baseline_id


def test_both_directions_are_actually_exercised(baseline_id: str) -> None:
    """The guard on the guard.

    If every mutation moved the id, the "does not move" tests would be the only
    thing standing between this feature and an identity that is pure noise; if
    none did, the "moves" tests would be all that stands between it and an
    identity that is a constant. Both lists must be non-empty, so this counts them.
    """
    moves = _score(golden=keyed_golden_set()).report_id != baseline_id
    holds = _score(repo_revision="another").report_id == baseline_id

    assert moves, "no mutation moved the id, so the identity is a constant"
    assert holds, "every mutation moved the id, so the identity is pure noise"
