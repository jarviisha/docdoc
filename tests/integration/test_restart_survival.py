"""Restart every process mid-run and nothing is left stuck (SC-011).

The property is narrower than it sounds and more useful: after a restart, no
operator action is required. Not "the run survives" — a run whose worker died is
*supposed* to be redone — but "every in-flight run reaches a terminal state on
its own".

What makes that true is that a worker holds **no state a restart cannot recover
from the run table**. There is no in-memory queue to drain, no registry of
workers to reconcile, and no lease to hand over: worker liveness *is* the lease,
and an expired one is a claim clause rather than a reaper's responsibility.

A restart is modelled as discarding every object and building new ones against
the same database — which is what a restart is, minus the process.
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
from docdoc.runs.model import DEFAULT_TENANT, TERMINAL_STATES
from docdoc.runs.postgres import PostgresRunQueue
from docdoc.runs.worker import execute_one

pytestmark = pytest.mark.postgres

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")


@dataclass
class Spec:
    blob_id: str
    tenant_id: str = DEFAULT_TENANT
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None


def _fresh_queue() -> PostgresRunQueue:
    """A queue built from nothing but the DSN — which is what a restart gets."""
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


@pytest.fixture
def clean_database() -> None:
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(require_database(), autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs")


def test_every_in_flight_run_reaches_a_terminal_state_after_a_restart(
    clean_database: None, tmp_path: Path
) -> None:
    """SC-011. Three runs, all claimed, every process replaced, no operator.

    The three are left in the state a `kill -9` leaves: claimed, leases held, no
    result written. Then everything is discarded and rebuilt, and a worker loop
    runs against the same database until nothing is claimable.
    """
    root = tmp_path / "store"
    blobs = BlobStore(root)
    blob_id = blobs.put(FIXTURE.read_bytes())

    before = _fresh_queue()
    now = datetime.now(UTC)
    run_ids = []
    for _ in range(3):
        submitted = before.submit(
            Spec(blob_id=blob_id),  # type: ignore[arg-type]
            run_id=new_run_id(),
            now=now,
            expires_at=now + timedelta(days=30),
        )
        run_ids.append(submitted.run_id)

    # Three workers claim, then die without finishing anything.
    for worker in ("w1", "w2", "w3"):
        assert (
            before.claim(worker_id=worker, now=now, lease=DEFAULT_LEASE, max_attempts=3) is not None
        )

    del before  # the restart

    after = _fresh_queue()
    registry = SchemaRegistry.from_paths([Path("schemas")])
    later = now + DEFAULT_LEASE + timedelta(seconds=1)

    drained = 0
    while drained < 10:  # bounded, so a stuck run fails rather than hangs
        claimed = after.claim(worker_id="restarted", now=later, lease=DEFAULT_LEASE, max_attempts=3)
        if claimed is None:
            break
        execute_one(
            claimed,
            queue=after,
            blobs=blobs,
            store=FileArtifactStore(root),
            registry=registry,
            adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
            now=later,
        )
        drained += 1

    stuck = [
        run_id
        for run_id in run_ids
        if (run := after.get(run_id, DEFAULT_TENANT)) is None or run.status not in TERMINAL_STATES
    ]
    assert not stuck, (
        f"{len(stuck)} runs are still non-terminal after a full restart. A worker "
        "holds no state a restart cannot recover from the run table, so nothing "
        "should need an operator to unwedge it"
    )


def test_a_restart_does_not_duplicate_work_already_finished(
    clean_database: None, tmp_path: Path
) -> None:
    """The other half: a restart must not redo what was already concluded.

    A run that succeeded before the restart stays succeeded, keeps its
    `processing_id`, and is never claimed again — which is what makes `finish`
    predicated on the run still being claimable rather than unconditional.
    """
    root = tmp_path / "store"
    blobs = BlobStore(root)
    blob_id = blobs.put(FIXTURE.read_bytes())

    before = _fresh_queue()
    now = datetime.now(UTC)
    submitted = before.submit(
        Spec(blob_id=blob_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=now,
        expires_at=now + timedelta(days=30),
    )
    claimed = before.claim(worker_id="w1", now=now, lease=DEFAULT_LEASE, max_attempts=3)
    assert claimed is not None
    execute_one(
        claimed,
        queue=before,
        blobs=blobs,
        store=FileArtifactStore(root),
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        now=now,
    )
    finished = before.get(submitted.run_id, DEFAULT_TENANT)
    assert finished is not None
    original = finished.processing_id

    del before

    after = _fresh_queue()
    much_later = now + DEFAULT_LEASE * 10
    assert (
        after.claim(worker_id="restarted", now=much_later, lease=DEFAULT_LEASE, max_attempts=3)
        is None
    ), "a completed run was handed out again after a restart"

    still = after.get(submitted.run_id, DEFAULT_TENANT)
    assert still is not None
    assert still.processing_id == original
