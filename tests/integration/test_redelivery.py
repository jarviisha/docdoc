"""A worker that dies mid-run costs one stage, not the run (SC-003, SC-004).

ADR-0013 §4 claims that at-least-once delivery is safe because re-execution is
mostly *resumption*: the stages that finished wrote artifacts, the redelivered
attempt reuses them, and only the stage in flight repeats. This is that claim,
measured.

**How process death is simulated, and why it is faithful.** A worker killed after
completing K stages leaves exactly one thing behind: a store holding K artifacts
and a claimed row whose lease will lapse. So the state is built directly —
execute a full run, then remove the artifacts of the stages that had not
finished — and the redelivered run is a fresh `execute_one` against it. Nothing
about killing a process matters to the pipeline beyond what it left on disk.

The measurement is a stage count, never elapsed time. SC-004 is a claim about how
much work is repeated, and a stopwatch answers a different question.
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
from docdoc.pipeline.stages import Stage
from docdoc.runs import migrations
from docdoc.runs.identity import DEFAULT_LEASE, new_run_id
from docdoc.runs.model import DEFAULT_TENANT, RunStatus
from docdoc.runs.postgres import PostgresRunQueue
from docdoc.runs.worker import execute_one

pytestmark = pytest.mark.postgres

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")

#: In order. `Stage` is an enum and the pipeline runs them in this sequence.
STAGES = (Stage.PARSE, Stage.EXTRACT, Stage.GROUND, Stage.VALIDATE)


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
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


def _run(queue: PostgresRunQueue, blobs: BlobStore, store: FileArtifactStore, blob_id: str):
    now = datetime.now(UTC)
    queue.submit(
        Spec(blob_id=blob_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=now,
        expires_at=now + timedelta(days=30),
    )
    claimed = queue.claim(worker_id="w", now=now, lease=DEFAULT_LEASE, max_attempts=3)
    assert claimed is not None
    result = execute_one(
        claimed,
        queue=queue,
        blobs=blobs,
        store=store,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        now=now,
    )
    assert result is not None
    return claimed.run_id, result


@pytest.mark.parametrize("completed", [0, 1, 2, 3])
def test_a_run_killed_after_n_stages_resumes_and_agrees(
    queue: PostgresRunQueue, tmp_path: Path, completed: int
) -> None:
    """SC-003 and SC-004, at each of the four boundaries.

    `completed` is how many stages the killed worker had finished. The
    redelivered run must reach the *same* `processing_id` and must execute
    exactly the stages that were missing — no more, because that would be paying
    twice, and no fewer, because that would be reusing something that was never
    written.
    """
    root = tmp_path / "store"
    blobs = BlobStore(root)
    store = FileArtifactStore(root)
    blob_id = blobs.put(FIXTURE.read_bytes())

    # The uninterrupted baseline, and the state a killed worker leaves.
    _, baseline = _run(queue, blobs, store, blob_id)
    assert baseline.executed_count == 4
    for stage in STAGES[completed:]:
        store.clear(stage=str(stage))

    run_id, redelivered = _run(queue, blobs, store, blob_id)

    assert redelivered.processing_id == baseline.processing_id, (
        "the redelivered run reached a different result. At-least-once delivery "
        "is only safe because re-executing a stage cannot disagree with itself "
        "(ADR-0013 §4)"
    )
    assert redelivered.executed_count == 4 - completed, (
        f"the worker had finished {completed} stages, so {4 - completed} should "
        f"have repeated; {redelivered.executed_count} did"
    )

    finished = queue.get(run_id, DEFAULT_TENANT)
    assert finished is not None
    assert finished.status is RunStatus.SUCCEEDED


def test_an_interruption_between_stages_repeats_nothing(
    queue: PostgresRunQueue, tmp_path: Path
) -> None:
    """SC-004's zero case, stated on its own because it is the good news.

    A worker killed *between* stages — all four artifacts written, the row not
    yet finished — costs nothing at all on redelivery. That is the common case
    for a crash, because the stages are where the time goes.
    """
    root = tmp_path / "store"
    blobs = BlobStore(root)
    store = FileArtifactStore(root)
    blob_id = blobs.put(FIXTURE.read_bytes())

    _, baseline = _run(queue, blobs, store, blob_id)
    _, redelivered = _run(queue, blobs, store, blob_id)

    assert redelivered.executed_count == 0
    assert redelivered.processing_id == baseline.processing_id
