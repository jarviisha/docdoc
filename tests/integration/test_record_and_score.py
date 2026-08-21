"""T077 — recording a prediction set and scoring it (FR-003, FR-037, contracts §7).

The other half of the loop. Everything else in this suite replays committed
predictions; this exercises the path that produced them, and the one property it
must hold above all others:

**A document that fails is recorded, never dropped.** Dropping it is the obvious
implementation — the loop continues, nothing raises, and the run looks robust.
What it does is remove that document from every denominator, so the pipeline
scores *higher* for having crashed. The failure has to survive into the report as
``MISSING`` labelled fields, and it has to bring the stage it stopped at with it.

Runs against the ``echo`` adapter, so it needs no credentials and no network. The
documents are supplied already parsed, which is the supported way to record
without a PDF reader — and the reason the offline suite can test this at all.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set, registry
from tests.fixtures.evaluation.predictions import RESPONSES, document_for

from docdoc.evaluation import (
    FieldOutcomeKind,
    Stage,
    evaluate,
    load_prediction_set,
)
from docdoc.extraction.adapters import EchoAdapter
from docdoc.recording import (
    RECORDER_ID,
    RECORDER_VERSION,
    record_predictions,
    write_prediction_set,
)

FACTS = facts_for_fixtures()

#: Every public fixture document, already parsed. Supplying them skips the parse,
#: which is what lets this run with `--extra dev` alone.
DOCUMENTS = {name: document_for(name) for name in RESPONSES if name != "invoice@2"}


def _adapter(name: str) -> EchoAdapter:
    return EchoAdapter.returning("invoice@1" if name.startswith("invoice") else "receipt@1", {})


def _one_document_set(name: str):  # type: ignore[no-untyped-def]
    golden = golden_set()
    return golden.model_copy(
        update={
            "documents": tuple(d for d in golden.documents if d.document_id == name),
            "labels": {name: golden.labels_for(name)},
        }
    )


def test_recording_produces_a_scoreable_prediction_set() -> None:
    """The loop closes: record, then score what was recorded."""
    golden = _one_document_set("clean")
    adapter = EchoAdapter.returning("invoice@1", RESPONSES["clean"])

    predictions = record_predictions(
        golden, adapter=adapter, registry=registry(), documents=DOCUMENTS
    )

    assert set(predictions.predictions) == {"clean"}
    report = evaluate(golden, predictions, facts=FACTS)
    assert report.metrics.micro["field_accuracy"].value == 1.0


def test_the_recorder_stamps_its_own_identity() -> None:
    """EVA-10: ``prediction_set_id`` folds the recorder, so it must have a version.

    A script under ``examples/`` would have none, and the prediction set's
    identity would carry a hole exactly where FR-040 and FR-042 need it whole.
    """
    predictions = record_predictions(
        _one_document_set("clean"),
        adapter=EchoAdapter.returning("invoice@1", RESPONSES["clean"]),
        registry=registry(),
        documents=DOCUMENTS,
    )

    assert predictions.recorder_id == RECORDER_ID
    assert predictions.recorder_version == RECORDER_VERSION


def test_a_failed_document_is_recorded_rather_than_dropped() -> None:
    """The rule this file exists for.

    The adapter answers with a shape the schema did not ask for, so extraction
    raises. The document must appear in the prediction set with its stage, not
    vanish from it.
    """
    golden = _one_document_set("clean")

    predictions = record_predictions(
        golden, adapter=EchoAdapter.malformed(), registry=registry(), documents=DOCUMENTS
    )

    assert "clean" in predictions.predictions, (
        "the document that failed is absent from the prediction set, so every "
        "metric computed from it silently excludes the hardest case"
    )
    recorded = predictions.predictions["clean"]
    assert recorded.failed_stage is Stage.EXTRACT
    assert not recorded.processed


def test_its_labelled_fields_count_as_missing() -> None:
    """FR-037, followed all the way through to the report."""
    golden = _one_document_set("clean")
    predictions = record_predictions(
        golden, adapter=EchoAdapter.malformed(), registry=registry(), documents=DOCUMENTS
    )

    report = evaluate(golden, predictions, facts=FACTS)

    assert {o.kind for o in report.outcomes} == {FieldOutcomeKind.MISSING, FieldOutcomeKind.CORRECT}
    assert report.metrics.counts.missing == 9, "the nine value labels"
    assert report.metrics.counts.correct_absence == 2, "absence is trivially satisfied"
    assert report.metrics.counts.labelled == 11, "and nothing left the denominator"


def test_the_failure_reason_is_a_class_name_and_never_a_value() -> None:
    """FR-057. An exception message can quote the content it choked on.

    This field reaches reports and logs, so it carries the type and nothing else.
    """
    predictions = record_predictions(
        _one_document_set("clean"),
        adapter=EchoAdapter.malformed(),
        registry=registry(),
        documents=DOCUMENTS,
    )
    recorded = predictions.predictions["clean"]

    assert recorded.failure_reason == "ExtractionError"
    assert " " not in (recorded.failure_reason or ""), "a message leaked into the reason"


def test_a_refusal_is_recorded_the_same_way() -> None:
    """A model that declines on content grounds is a failure, not an absence.

    It is a *successful* response whose stop reason says the model refused, which
    is precisely the case a naive loop records as "no values found" — and a
    document with no values found scores as entirely missing without anything
    saying why.
    """
    predictions = record_predictions(
        _one_document_set("clean"),
        adapter=EchoAdapter.refusing(category="safety"),
        registry=registry(),
        documents=DOCUMENTS,
    )
    recorded = predictions.predictions["clean"]

    assert recorded.failed_stage is Stage.EXTRACT
    assert recorded.failure_reason


def test_the_restricted_tier_is_not_recorded_unless_asked() -> None:
    """It needs a corpus this process may not have, so it is opt-in."""
    golden = golden_set()

    predictions = record_predictions(
        golden,
        adapter=EchoAdapter.returning("invoice@1", RESPONSES["clean"]),
        registry=registry(),
        documents=DOCUMENTS,
    )

    assert "restricted-invoice" not in predictions.predictions


def test_a_recorded_set_round_trips_through_disk(tmp_path: Path) -> None:
    """Recording, committing, and replaying must produce the same measurement.

    This is the whole basis of the public tier: predictions are recorded once and
    read back by contributors who cannot record them. If the round trip lost so
    much as a type, every replayed run would disagree with the run that produced
    the dataset.
    """
    golden = _one_document_set("clean")
    predictions = record_predictions(
        golden,
        adapter=EchoAdapter.returning("invoice@1", RESPONSES["clean"]),
        registry=registry(),
        documents=DOCUMENTS,
    )

    write_prediction_set(predictions, tmp_path)
    replayed = load_prediction_set(tmp_path, facts=FACTS)

    assert replayed.recorder_id == predictions.recorder_id
    assert replayed.recorder_version == predictions.recorder_version

    direct = evaluate(golden, predictions, facts=FACTS)
    from_disk = evaluate(golden, replayed, facts=FACTS)

    assert direct.report_id == from_disk.report_id, (
        "a replayed prediction set scores differently from the one that was "
        "recorded, so the committed dataset does not describe the run that made it"
    )


def test_recording_needs_no_parser_when_documents_are_supplied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The property that lets this whole file run with ``--extra dev`` alone."""
    import docdoc.ingest

    def explode(*args: object, **kwargs: object) -> None:
        raise AssertionError("record_predictions parsed a document it was handed")

    monkeypatch.setattr(docdoc.ingest, "parse", explode)

    predictions = record_predictions(
        _one_document_set("clean"),
        adapter=EchoAdapter.returning("invoice@1", RESPONSES["clean"]),
        registry=registry(),
        documents=DOCUMENTS,
    )

    assert predictions.predictions["clean"].processed


def test_the_recorded_prediction_carries_the_parser_that_made_the_document() -> None:
    """FR-040 requires the parser version, and the scorer never sees a document.

    The recorder is the only place that holds both, so it is the only place this
    can be recorded — and a report that could not state its parser would be a
    metric whose origin is partly unknown.
    """
    predictions = record_predictions(
        _one_document_set("clean"),
        adapter=EchoAdapter.returning("invoice@1", RESPONSES["clean"]),
        registry=registry(),
        documents=DOCUMENTS,
    )
    recorded = predictions.predictions["clean"]

    assert recorded.parser_id
    assert recorded.parser_version

    report = evaluate(_one_document_set("clean"), predictions, facts=FACTS)
    assert report.provenance.parser_ids == (recorded.parser_id,)
    assert report.provenance.parser_versions == (recorded.parser_version,)
