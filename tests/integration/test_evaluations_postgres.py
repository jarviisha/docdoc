"""An evaluation re-runs a tenant's checked documents and scores them, once."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from tests.infra import require_database

from docdoc.evaluation.corrections import Correction
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.runs import migrations, retention
from docdoc.runs.corrections import ANSWERS_NEVER_EXPIRE
from docdoc.runs.evaluations import EvaluationUnavailable, settled, start
from docdoc.runs.identity import DEFAULT_LEASE
from docdoc.runs.model import RunStatus
from docdoc.runs.postgres import PostgresRunQueue
from docdoc.runs.worker import execute_one

pytestmark = pytest.mark.postgres

TENANT = "acme"
PID = "sha256:" + "b" * 64
PDFS = (Path("tests/fixtures/pdf/digital_invoice.pdf"), Path("tests/fixtures/pdf/two_column.pdf"))


@pytest.fixture
def deployment(tmp_path):
    psycopg = pytest.importorskip("psycopg")
    from docdoc.api.app import _Deployment

    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs, corrections, run_tombstones, evaluations")
    return _Deployment(
        runs=PostgresRunQueue(lambda: psycopg.connect(dsn)),
        store_root=tmp_path,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
    )


def _checked(deployment, *paths: Path) -> list[str]:
    """Each document put in the tenant's store and one field of it checked."""
    _, blobs = deployment.stores_for(TENANT)
    blob_ids = []
    for path in paths:
        blob_id = blobs.put(path.read_bytes())
        deployment.corrections().record(
            Correction(
                report_id=PID,
                document_id=blob_id,
                field_path="total",
                predicted_value="1240.00",
                annotator="ana",
                timestamp=datetime(2026, 9, 9, tzinfo=UTC),
                reason="looked right",
                confirmed=True,
            ),
            tenant_id=TENANT,
            run_id=uuid4(),
            blob_id=blob_id,
            schema_name="invoice",
            at=datetime.now(UTC),
            expires_at=ANSWERS_NEVER_EXPIRE,
        )
        blob_ids.append(blob_id)
    return blob_ids


def _drain(deployment) -> None:
    """The worker, in process, until the queue is empty."""
    queue = deployment.runs()
    store, blobs = deployment.stores_for(TENANT)
    now = datetime.now(UTC)
    while run := queue.claim(worker_id="w", now=now, lease=DEFAULT_LEASE, max_attempts=3):
        execute_one(
            run,
            queue=queue,
            blobs=blobs,
            store=store,
            registry=deployment.registry(),
            adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
            now=now,
        )


def _finished(deployment):
    blob_ids = _checked(deployment, *PDFS)
    evaluation = start(deployment, tenant_id=TENANT, schema_name="invoice", at=datetime.now(UTC))
    _drain(deployment)
    return blob_ids, settled(deployment, evaluation, at=datetime.now(UTC))


def test_starting_submits_one_run_per_checked_document(deployment) -> None:
    blob_ids = _checked(deployment, *PDFS)

    evaluation = start(deployment, tenant_id=TENANT, schema_name="invoice", at=datetime.now(UTC))

    assert len(evaluation.run_ids) == 2
    assert sorted(evaluation.document_ids) == sorted(blob_ids)
    assert evaluation.schema_identity == "invoice@2"
    for run_id, blob_id in zip(evaluation.run_ids, evaluation.document_ids, strict=True):
        run = deployment.runs().get(run_id, TENANT)
        assert run.status is RunStatus.QUEUED
        assert run.blob_id == blob_id
        assert run.schema_identity == "invoice@2"
    stored = deployment.evaluations().get(evaluation.evaluation_id, tenant_id=TENANT)
    assert stored == evaluation
    assert deployment.evaluations().get(evaluation.evaluation_id, tenant_id="globex") is None


def test_an_unfinished_evaluation_has_no_report_yet(deployment) -> None:
    _checked(deployment, *PDFS)
    evaluation = start(deployment, tenant_id=TENANT, schema_name="invoice", at=datetime.now(UTC))

    after = settled(deployment, evaluation, at=datetime.now(UTC))

    assert after.report is None
    assert not after.finished
    stored = deployment.evaluations().get(evaluation.evaluation_id, tenant_id=TENANT)
    assert stored.report is None
    assert stored.settled_at is None


def test_a_finished_evaluation_gets_a_report_once(deployment) -> None:
    _, evaluation = _finished(deployment)

    assert evaluation.finished
    assert len(evaluation.report.document_scores) == 2
    stored = deployment.evaluations().get(evaluation.evaluation_id, tenant_id=TENANT)
    assert stored.report.report_id == evaluation.report.report_id

    again = settled(deployment, stored, at=datetime.now(UTC) + timedelta(hours=1))

    assert again.report.report_id == evaluation.report.report_id
    reread = deployment.evaluations().get(evaluation.evaluation_id, tenant_id=TENANT)
    assert reread.settled_at == stored.settled_at


def test_a_failed_run_counts_as_a_failed_document(deployment) -> None:
    gone, kept = _checked(deployment, *PDFS)
    evaluation = start(deployment, tenant_id=TENANT, schema_name="invoice", at=datetime.now(UTC))
    deployment.stores_for(TENANT)[1].delete(gone)
    _drain(deployment)

    evaluation = settled(deployment, evaluation, at=datetime.now(UTC))

    scores = {s.document_id: s for s in evaluation.report.document_scores}
    assert scores[gone].failed_stage is not None
    assert scores[kept].failed_stage is None


def test_a_second_start_while_unfinished_is_refused(deployment) -> None:
    _checked(deployment, *PDFS)
    start(deployment, tenant_id=TENANT, schema_name="invoice", at=datetime.now(UTC))

    with pytest.raises(EvaluationUnavailable) as raised:
        start(deployment, tenant_id=TENANT, schema_name="invoice", at=datetime.now(UTC))

    assert raised.value.code == "evaluation_in_progress"


def test_no_answers_is_refused(deployment) -> None:
    with pytest.raises(EvaluationUnavailable) as raised:
        start(deployment, tenant_id=TENANT, schema_name="invoice", at=datetime.now(UTC))

    assert raised.value.code == "no_answers"


def test_an_unregistered_schema_is_refused(deployment) -> None:
    with pytest.raises(EvaluationUnavailable) as raised:
        start(deployment, tenant_id=TENANT, schema_name="contract", at=datetime.now(UTC))

    assert raised.value.code == "unknown_schema"


def test_erasing_a_document_strips_it_and_keeps_the_aggregates(deployment) -> None:
    (erased, kept), before = _finished(deployment)
    store, blobs = deployment.stores_for(TENANT)

    retention.erase(
        deployment.runs(),
        retention.Stores(artifacts=store, blobs=blobs),
        tenant_id=TENANT,
        blob_id=erased,
        now=datetime.now(UTC),
    )

    after = deployment.evaluations().get(before.evaluation_id, tenant_id=TENANT)
    assert after.erased_documents == 1
    assert [d.document_id for d in after.golden.documents] == [kept]
    assert erased not in after.golden.labels
    assert all(o.document_id != erased for o in after.report.outcomes)
    assert [s.document_id for s in after.report.document_scores] == [kept]
    assert after.report.metrics == before.report.metrics
    assert after.report.report_id == before.report.report_id


def test_erasing_a_tenant_removes_its_evaluations(deployment) -> None:
    _, evaluation = _finished(deployment)
    store, blobs = deployment.stores_for(TENANT)

    retention.erase(
        deployment.runs(),
        retention.Stores(artifacts=store, blobs=blobs),
        tenant_id=TENANT,
        now=datetime.now(UTC),
    )

    assert deployment.evaluations().get(evaluation.evaluation_id, tenant_id=TENANT) is None
    assert deployment.evaluations().list(tenant_id=TENANT, schema_name=None, limit=10) == ()
