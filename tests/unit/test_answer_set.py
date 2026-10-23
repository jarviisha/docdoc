"""An answer set and a prediction, built from stored checks and runs."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from docdoc.evaluation import DocumentPrediction, PredictionSet, Stage, Tier, evaluate
from docdoc.extraction.registry import SchemaRegistry
from docdoc.runs.answers import answer_set, prediction_for
from docdoc.runs.corrections import Answer, Correction
from docdoc.runs.model import Run, RunStatus

REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 1, 1, tzinfo=UTC)
BLOB = "sha256:" + "a" * 64
OTHER = "sha256:" + "b" * 64
SCHEMA = SchemaRegistry.from_paths([REPO / "schemas"]).describe("invoice@1")


def answer(path: str, blob: str = BLOB, value: str = "1") -> Answer:
    return Answer(
        blob_id=blob,
        run_id=uuid4(),
        correction=Correction(
            report_id="sha256:" + "c" * 64,
            document_id=blob,
            field_path=path,
            predicted_value="0",
            corrected_value=value,
            reason="wrong",
            annotator="ops",
            timestamp=NOW,
        ),
    )


def build(*answers: Answer):
    return answer_set(answers, tenant_id="t1", schema=SCHEMA)


def test_documents_are_restricted_and_stamped_with_the_schema_run():
    golden = build(answer("total"))
    assert golden.documents[0].tier is Tier.RESTRICTED
    assert golden.documents[0].schema_identity == "invoice@1"
    assert golden.golden_set_id


def test_a_field_the_new_version_dropped_is_not_scored():
    golden = build(answer("no_such_field"), answer("total"))
    assert [label.field_path for label in golden.labels[BLOB]] == ["total"]
    assert golden.documents[0].declared_label_count == 1


def test_entry_indices_match_the_schema_template():
    golden = build(answer("line_items[1].amount"))
    assert [label.field_path for label in golden.labels[BLOB]] == ["line_items[1].amount"]


def test_a_document_left_with_no_labels_is_dropped():
    golden = build(answer("no_such_field"), answer("total", blob=OTHER))
    assert [d.document_id for d in golden.documents] == [OTHER]
    assert BLOB not in golden.labels


def run(status: RunStatus, **extra) -> Run:
    return Run(
        run_id=uuid4(),
        tenant_id="t1",
        blob_id=BLOB,
        schema_identity="invoice@1",
        status=status,
        expires_at=NOW,
        created_at=NOW,
        updated_at=NOW,
        **extra,
    )


def test_a_failed_run_becomes_a_failed_prediction():
    prediction = prediction_for(
        None, run(RunStatus.FAILED, failed_stage="extract", error_class="X"), BLOB
    )
    assert prediction.failed_stage is Stage.EXTRACT
    assert prediction.failure_reason == "X"
    assert prediction.extraction is None


def test_an_unknown_stage_falls_back_to_parse():
    prediction = prediction_for(None, run(RunStatus.CANCELLED), BLOB)
    assert prediction.failed_stage is Stage.PARSE
    assert prediction.failure_reason == "run_cancelled"


def test_evaluate_accepts_the_pair():
    golden = build(answer("total"))
    failed = DocumentPrediction(document_id=BLOB, failed_stage=Stage.EXTRACT, failure_reason="X")
    report = evaluate(golden, PredictionSet(predictions={BLOB: failed}))
    assert "field_accuracy" in report.metrics.micro


def _stored_run(tmp_path):
    # SC-013: a real parse needs the native PDF reader, which the base install lacks.
    pytest.importorskip("pypdfium2", reason="a real parse is what gets stored")
    from docdoc.artifacts import FileArtifactStore
    from docdoc.extraction.adapters.echo import EchoAdapter
    from docdoc.pipeline import run as run_pipeline

    store = FileArtifactStore(tmp_path)
    result = run_pipeline(
        (REPO / "tests/fixtures/pdf/digital_invoice.pdf").read_bytes(),
        schema="invoice@1",
        registry=SchemaRegistry.from_paths([REPO / "schemas"]),
        adapter=EchoAdapter.from_fixtures(REPO / "tests/fixtures/echo"),
        store=store,
    )
    return store, result


def test_a_succeeded_run_becomes_its_stored_prediction(tmp_path):
    store, result = _stored_run(tmp_path)
    the_run = run(RunStatus.SUCCEEDED, processing_id=result.validation.artifact_id)
    prediction = prediction_for(store, the_run, BLOB)
    assert prediction.failed_stage is None
    assert prediction.validation.artifact_id == result.validation.artifact_id
    assert prediction.extraction.artifact_id == result.extraction.artifact_id
    assert prediction.grounding.artifact_id == result.grounding.artifact_id
    assert prediction.parser_id
    assert prediction.parser_version


def test_a_succeeded_run_with_a_missing_artifact_is_a_failed_prediction(tmp_path):
    store, _ = _stored_run(tmp_path)
    the_run = run(RunStatus.SUCCEEDED, processing_id="sha256:" + "f" * 64)
    prediction = prediction_for(store, the_run, BLOB)
    assert prediction.failed_stage is Stage.VALIDATE
    assert prediction.failure_reason == "artifact_missing"


def test_a_non_validation_id_is_a_failed_prediction(tmp_path):
    store, result = _stored_run(tmp_path)
    the_run = run(RunStatus.SUCCEEDED, processing_id=result.extraction.artifact_id)
    prediction = prediction_for(store, the_run, BLOB)
    assert prediction.failed_stage is Stage.VALIDATE
    assert prediction.failure_reason == "artifact_missing"


def test_an_unreadable_artifact_is_a_failed_prediction():
    from docdoc.artifacts import ArtifactError

    class Corrupt:
        def envelope(self, artifact_id):
            return None

        def get(self, artifact_id, *, model, artifact_format_version):
            raise ArtifactError("bad", reason="integrity", artifact_id=artifact_id, root="/x")

    prediction = prediction_for(Corrupt(), run(RunStatus.SUCCEEDED, processing_id="p"), BLOB)
    assert prediction.failed_stage is Stage.VALIDATE
    assert prediction.failure_reason == "artifact_unreadable"
