"""Checks are answers: the newest per (tenant, schema name, blob, field) wins."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from tests.infra import require_database

from docdoc.evaluation.corrections import Correction
from docdoc.runs import migrations
from docdoc.runs.corrections import ANSWERS_NEVER_EXPIRE, PostgresCorrectionStore
from docdoc.runs.postgres import PostgresRunQueue

pytestmark = pytest.mark.postgres

PID = "sha256:" + "b" * 64
BLOB_1 = "sha256:" + "1" * 64
BLOB_2 = "sha256:" + "2" * 64


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs, corrections, run_tombstones")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


@pytest.fixture
def store(queue: PostgresRunQueue) -> PostgresCorrectionStore:
    return PostgresCorrectionStore(execute=queue._execute)


def _check(blob: str, field: str = "total", **kw) -> Correction:
    return Correction(
        report_id=PID,
        document_id=blob,
        field_path=field,
        predicted_value=kw.pop("predicted_value", "228.00"),
        annotator="ana",
        timestamp=datetime(2026, 9, 9, tzinfo=UTC),
        **kw,
    )


def _record(store, correction, *, run_id, blob, at, tenant="a", schema="invoice"):
    return store.record(
        correction,
        tenant_id=tenant,
        run_id=run_id,
        blob_id=blob,
        schema_name=schema,
        at=at,
        expires_at=ANSWERS_NEVER_EXPIRE,
    )


def test_the_newest_check_on_any_run_wins(store: PostgresCorrectionStore) -> None:
    from uuid import uuid4

    now = datetime.now(UTC)
    run1, run2 = uuid4(), uuid4()
    _record(
        store,
        _check(BLOB_1, corrected_value="190.00", reason="typo"),
        run_id=run1,
        blob=BLOB_1,
        at=now,
    )
    _record(
        store,
        _check(BLOB_1, confirmed=True),
        run_id=run2,
        blob=BLOB_1,
        at=now + timedelta(seconds=1),
    )

    (answer,) = store.answers(tenant_id="a", schema_name="invoice")

    assert answer.run_id == run2
    assert answer.blob_id == BLOB_1
    assert answer.correction.confirmed


def test_answers_are_per_tenant_and_per_schema_name(store: PostgresCorrectionStore) -> None:
    from uuid import uuid4

    now = datetime.now(UTC)
    _record(store, _check(BLOB_1, confirmed=True), run_id=uuid4(), blob=BLOB_1, at=now)
    _record(
        store,
        _check(BLOB_1, confirmed=True),
        run_id=uuid4(),
        blob=BLOB_1,
        at=now,
        tenant="b",
        schema="receipt",
    )

    assert store.answers(tenant_id="b", schema_name="invoice") == ()
    assert len(store.answers(tenant_id="a", schema_name="invoice")) == 1


def test_a_run_with_an_answer_is_pinned(store: PostgresCorrectionStore) -> None:
    from uuid import uuid4

    now = datetime.now(UTC)
    run_id = uuid4()
    _record(store, _check(BLOB_1, confirmed=True), run_id=run_id, blob=BLOB_1, at=now)

    pinned = store.pinned_runs([run_id], at=now + timedelta(days=36500))

    assert run_id in pinned


def test_the_summary_counts_documents_and_fields(store: PostgresCorrectionStore) -> None:
    from uuid import uuid4

    now = datetime.now(UTC)
    for blob, field in ((BLOB_1, "total"), (BLOB_1, "date"), (BLOB_2, "total")):
        _record(store, _check(blob, field, confirmed=True), run_id=uuid4(), blob=blob, at=now)

    assert store.answer_summary(tenant_id="a") == (("invoice", 2, 3),)
