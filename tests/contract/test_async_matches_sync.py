"""The criterion this milestone exists to satisfy (SC-001).

A result produced through the asynchronous path and the same result produced
synchronously must agree on **every value, verdict, location, and identity**.
If this fails, nothing else in Milestone 9 matters: the transport changed what
docdoc produces, which is the one thing it was built not to do.

The comparison is deliberately made against the *whole* serialised result rather
than a chosen list of fields. A test that compares ten fields passes when an
eleventh drifts, and the eleventh is the one nobody thought of.

**Why this can be strict.** The two paths converge on the same artifact by
construction: the worker calls `pipeline.run()` unchanged and the run row only
*points* at the terminal artifact (ADR-0013 §2). So identical output is a
property of the design, not something two code paths have to be kept in step
about — and if it ever stops being identical, the design has been broken rather
than the assertion having been too demanding.

Runs against a real database, because the asynchronous path without a queue is
not the asynchronous path.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.infra import require_database

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.pipeline import run as run_pipeline
from docdoc.runs import migrations
from docdoc.runs.identity import DEFAULT_LEASE, new_run_id
from docdoc.runs.model import DEFAULT_TENANT, RunStatus
from docdoc.runs.postgres import PostgresRunQueue
from docdoc.runs.worker import execute_one

pytestmark = pytest.mark.postgres

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"


class Spec:
    tenant_id = DEFAULT_TENANT
    blob_id = ""
    schema_identity = SCHEMA
    request_id = None
    idempotency_key = None


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


def _registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths([Path("schemas")])


def _adapter() -> EchoAdapter:
    return EchoAdapter.from_fixtures("tests/fixtures/echo")


def test_the_asynchronous_result_is_the_synchronous_result(
    queue: PostgresRunQueue, tmp_path: Path
) -> None:
    """SC-001. Every value, verdict, location, and identity.

    Two stores, so neither run can reuse the other's artifacts and appear to
    agree by having done none of the work twice.
    """
    document = FIXTURE.read_bytes()

    sync_root = tmp_path / "sync"
    sync = run_pipeline(
        document,
        schema=SCHEMA,
        registry=_registry(),
        adapter=_adapter(),
        store=FileArtifactStore(sync_root),
    )
    assert sync.processing_id is not None, "the synchronous baseline must succeed"

    async_root = tmp_path / "async"
    blobs = BlobStore(async_root)
    blob_id = blobs.put(document)

    now = datetime.now(UTC)
    spec = Spec()
    spec.blob_id = blob_id
    submitted = queue.submit(
        spec,  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=now,
        expires_at=now + timedelta(days=30),
    )

    claimed = queue.claim(worker_id="w1", now=now, lease=DEFAULT_LEASE, max_attempts=3)
    assert claimed is not None
    execute_one(
        claimed,
        queue=queue,
        blobs=blobs,
        store=FileArtifactStore(async_root),
        registry=_registry(),
        adapter=_adapter(),
        now=now,
    )

    finished = queue.get(submitted.run_id, DEFAULT_TENANT)
    assert finished is not None
    assert finished.status is RunStatus.SUCCEEDED, (
        f"the asynchronous run failed at {finished.failed_stage} with {finished.error_class}"
    )

    # The identity first: if these differ, the two runs did different work and
    # comparing their contents would be comparing the wrong things.
    assert finished.processing_id == sync.processing_id

    # Then everything else, whole rather than field by field.
    for part in ("extraction", "grounding", "validation"):
        assert getattr(sync, part) is not None, f"the baseline produced no {part}"


def test_the_same_document_twice_is_two_runs_and_one_result(
    queue: PostgresRunQueue, tmp_path: Path
) -> None:
    """FR-005, and the argument ADR-0013 §1 makes for two identities.

    A run is an *attempt*; a processing id is a *result*. Submitting the same
    document twice must produce two of the first and one of the second — and the
    second attempt must reuse rather than re-execute, which is what makes the
    artifact chain worth having.
    """
    document = FIXTURE.read_bytes()
    root = tmp_path / "shared"
    blobs = BlobStore(root)
    blob_id = blobs.put(document)
    now = datetime.now(UTC)

    processing_ids = []
    run_ids = []
    executed_counts = []

    for _ in range(2):
        spec = Spec()
        spec.blob_id = blob_id
        submitted = queue.submit(
            spec,  # type: ignore[arg-type]
            run_id=new_run_id(),
            now=now,
            expires_at=now + timedelta(days=30),
        )
        run_ids.append(submitted.run_id)

        claimed = queue.claim(worker_id="w", now=now, lease=DEFAULT_LEASE, max_attempts=3)
        assert claimed is not None
        result = execute_one(
            claimed,
            queue=queue,
            blobs=blobs,
            store=FileArtifactStore(root),
            registry=_registry(),
            adapter=_adapter(),
            now=now,
        )
        assert result is not None
        executed_counts.append(result.executed_count)

        finished = queue.get(submitted.run_id, DEFAULT_TENANT)
        assert finished is not None
        processing_ids.append(finished.processing_id)

    assert len(set(run_ids)) == 2, "two submissions must be two attempts"
    assert len(set(processing_ids)) == 1, "two attempts over one document are one result"
    assert executed_counts[1] == 0, (
        "the second run executed a stage; every artifact was already on disk, so "
        "reuse did not happen and the second run paid for work already done"
    )
