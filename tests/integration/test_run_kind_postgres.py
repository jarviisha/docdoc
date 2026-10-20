"""Migration 0008 against a real database: a kind, and a schema only extraction has."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from tests.infra import require_database

from docdoc.runs import migrations
from docdoc.runs.identity import new_run_id
from docdoc.runs.postgres import PostgresRunQueue

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@dataclass
class Milestone9Spec:
    """Four strings and nothing about kind: what every caller sent before 014."""

    tenant_id: str = "default"
    blob_id: str = "sha256:blob"
    schema_identity: str | None = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None


@dataclass
class OcrSpec(Milestone9Spec):
    schema_identity: str | None = None
    kind: str = "ocr"
    ocr_parser: str | None = "paddle"
    ocr_language: str | None = "vie"


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


def _submit(queue: PostgresRunQueue, spec: object):  # type: ignore[no-untyped-def]
    return queue.submit(
        spec,  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=NOW,
        expires_at=NOW + timedelta(days=1),
    )


def test_migration_0008_keeps_existing_rows_extraction(queue: PostgresRunQueue) -> None:
    run = _submit(queue, Milestone9Spec())
    assert queue.get(run.run_id, "default").kind == "extraction"  # type: ignore[union-attr]


def test_ocr_run_round_trips(queue: PostgresRunQueue) -> None:
    run = _submit(queue, OcrSpec())
    read = queue.get(run.run_id, "default")
    assert read is not None
    assert (read.kind, read.schema_identity, read.ocr_parser, read.ocr_language) == (
        "ocr",
        None,
        "paddle",
        "vie",
    )


def test_ocr_row_with_a_schema_violates_the_constraint() -> None:
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(require_database(), autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        with pytest.raises(psycopg.errors.CheckViolation):
            connection.execute(
                "INSERT INTO runs (run_id, tenant_id, blob_id, schema_identity, status, kind,"
                " created_at, updated_at, expires_at)"
                " VALUES (%s, 'default', 'b', 'invoice@1', 'queued', 'ocr', %s, %s, %s)",
                (new_run_id(), NOW, NOW, NOW),
            )
