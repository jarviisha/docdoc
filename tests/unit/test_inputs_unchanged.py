"""T053 — evaluation reads its inputs and writes nothing (FR-006, SC-008).

A scorer that modified its dataset would be unfalsifiable. Every subsequent run
would score against something the previous run produced, the golden set would
drift toward whatever the pipeline happens to do, and the numbers would improve
for a reason nobody could find -- because the evidence of the change is the thing
that was changed.

The check is a hash either side of a run, over the bytes on disk. Comparing
objects would miss a write-then-rewrite, and comparing modification times would
miss a same-content rewrite that lost precision somewhere.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from docdoc.evaluation import evaluate, load_golden_set, load_prediction_set
from tests.fixtures.evaluation.datasets import (
    DOCUMENTS,
    facts_for_fixtures,
    golden_set,
)
from tests.fixtures.evaluation.predictions import prediction_set


def _digest(root: Path) -> dict[str, str]:
    """sha256 of every file under ``root``, by relative path."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@pytest.fixture
def dataset_on_disk(tmp_path: Path) -> Path:
    """A golden set and a prediction set written out as a maintainer would have them."""
    golden = golden_set(include_restricted=False)
    labels_dir = tmp_path / "labels"
    predictions_dir = tmp_path / "predictions"
    labels_dir.mkdir()
    predictions_dir.mkdir()

    manifest = {
        "documents": [json.loads(document.model_dump_json()) for document in golden.documents]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    for name, _schema, labels in DOCUMENTS:
        payload = [json.loads(label.model_dump_json()) for label in labels]
        (labels_dir / f"{name}.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for document_id, prediction in prediction_set().predictions.items():
        (predictions_dir / f"{document_id}.json").write_text(
            prediction.model_dump_json(indent=2), encoding="utf-8"
        )

    return tmp_path


def test_the_inputs_are_byte_identical_after_a_run(dataset_on_disk: Path) -> None:
    """The assertion. Nothing under the dataset root changes, at all."""
    facts = facts_for_fixtures()
    before = _digest(dataset_on_disk)

    golden = load_golden_set(dataset_on_disk / "manifest.json", facts=facts)
    predictions = load_prediction_set(dataset_on_disk / "predictions", facts=facts)
    evaluate(golden, predictions, facts=facts)

    after = _digest(dataset_on_disk)

    assert before == after, (
        "the dataset changed during evaluation. A scorer that writes to its own "
        "inputs cannot be checked by anything, because the evidence is the thing "
        "that moved"
    )


def test_the_digest_would_notice_a_change(dataset_on_disk: Path) -> None:
    """The guard on the guard. A hash over nothing compares equal to a hash over nothing."""
    before = _digest(dataset_on_disk)
    assert len(before) >= 10, f"expected a dataset of files, found {sorted(before)}"

    manifest = dataset_on_disk / "manifest.json"
    manifest.write_text(manifest.read_text(encoding="utf-8") + "\n", encoding="utf-8")

    assert _digest(dataset_on_disk) != before


def test_no_new_file_appears(dataset_on_disk: Path) -> None:
    """FR-006 covers creation as much as modification.

    A cache written next to the dataset is still a write, and it is the one a
    contributor is most likely to commit by accident.
    """
    facts = facts_for_fixtures()
    before = set(_digest(dataset_on_disk))

    golden = load_golden_set(dataset_on_disk / "manifest.json", facts=facts)
    evaluate(golden, load_prediction_set(dataset_on_disk / "predictions", facts=facts), facts=facts)

    assert set(_digest(dataset_on_disk)) == before


def test_the_in_memory_objects_are_unchanged_too() -> None:
    """The models are frozen, so this holds structurally rather than by care.

    Asserted anyway: ``model_copy`` and a mutable default are both one review
    away, and either would make a second evaluation score against the first's
    leftovers.
    """
    golden = golden_set()
    predictions = prediction_set()
    facts = facts_for_fixtures()

    golden_before = golden.model_dump_json()
    predictions_before = predictions.model_dump_json()

    evaluate(golden, predictions, facts=facts)
    evaluate(golden, predictions, facts=facts)

    assert golden.model_dump_json() == golden_before
    assert predictions.model_dump_json() == predictions_before
