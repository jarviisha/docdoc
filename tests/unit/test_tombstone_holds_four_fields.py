"""T051 — what remains of a removed run, and what it refuses to remain.

Four fields (FR-004). The shortness is the specification, not an economy: a
tombstone that carried what the run held would be a way of retaining what was
deleted, and the obvious additions are the tempting ones — the blob it named, the
schema it ran, how many stages it completed.

**`policy` is why `RunStatus` gains no member** (FR-004a). Ageing out and being
erased on request are different events and an operator needs to tell them apart;
once that distinction is recorded here, a status carrying it too would be a second
source of truth about one fact, and the two would eventually disagree.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from docdoc.artifacts.blobs import BlobStore
from docdoc.artifacts.store import NullArtifactStore
from docdoc.runs import retention
from docdoc.runs.model import Run, RunStatus, Tombstone
from tests.fixtures.run_queue import InMemoryRunQueue

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _expired_run(tenant_id: str = "acme") -> Run:
    return Run(
        run_id=uuid4(),
        tenant_id=tenant_id,
        blob_id="sha256:" + "ab" * 32,
        schema_identity="invoice@1",
        status=RunStatus.SUCCEEDED,
        processing_id="sha256:" + "cd" * 32,
        created_at=NOW - timedelta(days=90),
        updated_at=NOW - timedelta(days=90),
        expires_at=NOW - timedelta(days=60),
    )


def test_four_fields_and_a_fifth_is_refused() -> None:
    stone = Tombstone(
        run_id=uuid4(), tenant_id="acme", deleted_at=NOW, policy=retention.POLICY_RETENTION
    )

    assert set(stone.model_dump()) == {"run_id", "tenant_id", "deleted_at", "policy"}

    with pytest.raises(ValidationError):
        Tombstone(
            run_id=uuid4(),
            tenant_id="acme",
            deleted_at=NOW,
            policy=retention.POLICY_RETENTION,
            blob_id="sha256:" + "ab" * 32,  # type: ignore[call-arg]
        )


def test_it_is_frozen() -> None:
    """A record of a deletion that could be edited is not a record."""
    stone = Tombstone(
        run_id=uuid4(), tenant_id="acme", deleted_at=NOW, policy=retention.POLICY_RETENTION
    )
    with pytest.raises(ValidationError):
        stone.policy = "something-else"  # type: ignore[misc]


def test_the_policy_distinguishes_ageing_out_from_being_erased(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """The distinction no run status carries, carried here instead."""
    queue = InMemoryRunQueue()
    swept, erased = _expired_run(), _expired_run()
    queue._runs[swept.run_id] = swept
    queue._runs[erased.run_id] = erased

    stores = retention.Stores(
        artifacts=NullArtifactStore(), blobs=BlobStore(tmp_path, tenant_id="acme")
    )
    retention.sweep(queue, lambda _t: stores, now=NOW, batch=1)
    # `erase` finds its own runs now: an erasure is "this customer's data", not
    # "these run ids", and a caller passing a list could pass a stale one.
    retention.erase(queue, stores, tenant_id="acme", now=NOW)

    assert queue.tombstone(swept.run_id, "acme").policy == "retention"  # type: ignore[union-attr]
    assert queue.tombstone(erased.run_id, "acme").policy == "erasure:tenant"  # type: ignore[union-attr]


def test_another_tenant_reads_no_tombstone(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """FR-011: to everyone else it is an identifier that never existed."""
    queue = InMemoryRunQueue()
    run = _expired_run()
    queue._runs[run.run_id] = run

    retention.sweep(
        queue,
        lambda _t: retention.Stores(
            artifacts=NullArtifactStore(), blobs=BlobStore(tmp_path, tenant_id="acme")
        ),
        now=NOW,
        batch=10,
    )

    assert queue.tombstone(run.run_id, "acme") is not None
    assert queue.tombstone(run.run_id, "globex") is None
