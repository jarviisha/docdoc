"""A failure nobody was holding a connection for is still fully retrievable.

This is User Story 2, and it exists because of a sentence in `api/app.py`: when a
synchronous run fails, "this response is the only place the completed stages'
results can appear". That is sound while the caller is holding the response.
Asynchronously the caller is holding nothing, so a failed run that is not
persisted is a run that silently never happened.

Three failure shapes, and the run record must tell them apart, because they send
an operator to three different places:

* a stage failed          -> look at the document or the provider
* the schema was withdrawn -> look at the deployment's configuration (FR-091)
* the attempt limit ran out -> look at the document; it is killing workers
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.infra import require_database

from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.runs import migrations
from docdoc.runs.identity import DEFAULT_LEASE, new_run_id
from docdoc.runs.model import DEFAULT_TENANT, RunStatus
from docdoc.runs.postgres import PostgresRunQueue
from docdoc.runs.worker import execute_one

pytestmark = pytest.mark.postgres

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
LEASE = DEFAULT_LEASE


@dataclass
class Spec:
    blob_id: str
    tenant_id: str = DEFAULT_TENANT
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


def _claimed(queue: PostgresRunQueue, blobs: BlobStore, *, schema: str = "invoice@1"):
    blob_id = blobs.put(FIXTURE.read_bytes())
    now = datetime.now(UTC)
    queue.submit(
        Spec(blob_id=blob_id, schema_identity=schema),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=now,
        expires_at=now + timedelta(days=30),
    )
    claimed = queue.claim(worker_id="w1", now=now, lease=LEASE, max_attempts=3)
    assert claimed is not None
    return claimed, now


def test_a_stage_failure_names_the_stage_and_the_error_class(
    queue: PostgresRunQueue, tmp_path: Path
) -> None:
    """FR-035, FR-036: what failed, and what the stages before it produced."""
    blobs = BlobStore(tmp_path)
    claimed, now = _claimed(queue, blobs)

    execute_one(
        claimed,
        queue=queue,
        blobs=blobs,
        store=FileArtifactStore(tmp_path),
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        # The repository's own failure mode, not a hand-rolled fake. A fake
        # adapter missing part of the interface raises `AttributeError`, which
        # the pipeline attributes to `validate` — so the test would have passed
        # for the wrong reason, or failed while blaming the wrong stage.
        adapter=EchoAdapter(mode="provider_error"),
        now=now,
    )

    run = queue.get(claimed.run_id, DEFAULT_TENANT)
    assert run is not None
    assert run.status is RunStatus.FAILED
    assert run.failed_stage == "extract"
    assert run.error_class == "ModelProviderError"
    assert run.processing_id is None, "a failed run has no terminal artifact"

    # The stages that did complete are kept, which is the whole point: the caller
    # was not holding a response, so this row is the only record there is.
    stages = {outcome.stage for outcome in run.stage_outcomes}
    assert "parse" in stages, f"the parse outcome was lost; kept {sorted(stages)}"


def test_a_withdrawn_schema_fails_once_and_names_no_stage(
    queue: PostgresRunQueue, tmp_path: Path
) -> None:
    """FR-091. A configuration fault, and it must not be called abandonment.

    The run is claimed once and comes to rest. Retrying would spend the attempt
    budget and then report `RunAbandonedError`, which would send an operator to
    look at a document that is perfectly fine.
    """
    blobs = BlobStore(tmp_path)
    claimed, now = _claimed(queue, blobs, schema="withdrawn@1")

    result = execute_one(
        claimed,
        queue=queue,
        blobs=blobs,
        store=FileArtifactStore(tmp_path),
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        now=now,
    )

    assert result is None, "the pipeline must not have been reached"

    run = queue.get(claimed.run_id, DEFAULT_TENANT)
    assert run is not None
    assert run.status is RunStatus.FAILED
    assert run.failed_stage is None, "no stage was reached, so none can be named"
    assert run.error_class != "RunAbandonedError"
    assert run.attempts == 1, "claimed once, and terminal on that first claim"
    assert run.stage_outcomes == ()

    # And it is not claimable again, at any later instant.
    later = now + LEASE * 5
    assert queue.claim(worker_id="w2", now=later, lease=LEASE, max_attempts=3) is None


def test_a_run_that_keeps_losing_its_worker_comes_to_rest_as_abandoned(
    queue: PostgresRunQueue, tmp_path: Path
) -> None:
    """SC-006, through the queue that a dying worker leaves behind.

    Process death is simulated by claiming and never finishing — which is exactly
    what a killed worker leaves: a claimed row whose lease lapses. Without the
    attempt limit this loop never terminates, which is the point of having one.
    """
    blobs = BlobStore(tmp_path)
    blob_id = blobs.put(FIXTURE.read_bytes())
    now = datetime.now(UTC)
    submitted = queue.submit(
        Spec(blob_id=blob_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=now,
        expires_at=now + timedelta(days=30),
    )

    at = now
    workers_killed = 0
    for _ in range(10):  # bounded so a broken limit fails rather than hangs
        claimed = queue.claim(worker_id=f"w{workers_killed}", now=at, lease=LEASE, max_attempts=3)
        if claimed is None:
            break
        workers_killed += 1
        at += LEASE + timedelta(seconds=1)

    assert workers_killed == 3, (
        f"the document was handed to {workers_killed} workers; the attempt limit "
        "is what stops it taking the whole pool one process at a time"
    )

    run = queue.get(submitted.run_id, DEFAULT_TENANT)
    assert run is not None
    assert run.status is RunStatus.FAILED
    assert run.error_class == "RunAbandonedError"
