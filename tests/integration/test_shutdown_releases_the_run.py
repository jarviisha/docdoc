"""FR-042 and FR-043 — a signalled worker gives the run back, it does not sit on it.

The gap this closes had no symptom you could see from inside the process. The
worker consulted its shutdown flag only *after* `execute_one` returned, so a
signalled worker always ran the current document to completion. Since a run takes
minutes and an orchestrator's grace period is seconds, what actually happened was:
`SIGTERM`, worker keeps parsing, `SIGKILL`, and the run then waits out its **full
lease** before anyone can pick it up. `release()` existed on the protocol and on
both implementations, and nothing called it.

So the assertion is about *when the run becomes claimable*, not about whether it
eventually does. Both are true of a lease that simply expires; only one is true of
a worker that let go.

Two properties, and the second is what stops the fix being worse than the gap:

* the run returns to `queued` at the instant of shutdown, claimable immediately;
* it is **not** marked `cancelled`. Nobody asked for that, and a terminal state
  naming a cancellation nobody requested would lose the work permanently rather
  than delay it.

Driven at arbitrary instants against the in-memory queue: "immediately rather
than after the lease" is a claim about two timestamps, and sleeping through a
ninety-second lease to check it would be the slowest possible way to learn
nothing extra.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pymupdf", reason="a real multi-stage run is what gets interrupted")

from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.pipeline.result import StageStatus
from docdoc.pipeline.stages import Stage
from docdoc.runs.identity import DEFAULT_LEASE, new_run_id
from docdoc.runs.model import DEFAULT_TENANT, RunStatus
from docdoc.runs.worker import execute_one

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


@dataclass
class Spec:
    blob_id: str
    tenant_id: str = DEFAULT_TENANT
    schema_identity: str = SCHEMA
    request_id: str | None = None
    idempotency_key: str | None = None


@pytest.fixture
def claimed(tmp_path: Path):
    """A run in flight: submitted, claimed, and about to be interrupted."""
    queue = InMemoryRunQueue()
    blobs = BlobStore(tmp_path)
    blob_id = blobs.put(FIXTURE.read_bytes())
    queue.submit(
        Spec(blob_id=blob_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    run = queue.claim(worker_id="w1", now=NOW, lease=DEFAULT_LEASE, max_attempts=3)
    assert run is not None
    return queue, blobs, FileArtifactStore(tmp_path), run


def _execute(claimed, *, stopping) -> Any:  # type: ignore[no-untyped-def]
    queue, blobs, store, run = claimed
    return execute_one(
        run,
        queue=queue,
        blobs=blobs,
        store=store,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        now=NOW,
        stopping=stopping,
    )


def test_a_shutdown_returns_the_run_to_the_queue_immediately(claimed) -> None:  # type: ignore[no-untyped-def]
    """FR-043. Claimable at the instant of shutdown, not a lease later.

    The second claim is made at `NOW` — the same instant, with the lease very
    much unexpired. Under the old behaviour it returned `None`, and a replacement
    worker had ninety seconds of nothing to do.
    """
    queue, _blobs, _store, run = claimed

    _execute(claimed, stopping=lambda: True)

    state = queue.get(run.run_id, DEFAULT_TENANT)
    assert state is not None
    assert state.status is RunStatus.QUEUED, (
        f"the run rests at {state.status}; a signalled worker must give it back"
    )
    assert state.worker_id is None
    assert state.lease_until is None

    immediately = queue.claim(worker_id="w2", now=NOW, lease=DEFAULT_LEASE, max_attempts=3)
    assert immediately is not None, (
        "the run was not claimable at the instant of shutdown, so a rolling "
        "restart still costs a full lease duration per replica — which is the "
        "whole of what FR-043 asks for"
    )
    assert immediately.run_id == run.run_id


def test_the_interrupted_run_is_not_recorded_as_cancelled(claimed) -> None:  # type: ignore[no-untyped-def]
    """The failure mode the fix could have introduced, pinned.

    A run stopped at a stage boundary looks exactly like a cancelled one from
    `PipelineResult`: no `failed_stage`, no `processing_id`. Recording it as
    `cancelled` would be terminal — the work would be lost rather than delayed,
    and no operator action would bring it back.
    """
    queue, _blobs, _store, run = claimed

    _execute(claimed, stopping=lambda: True)

    state = queue.get(run.run_id, DEFAULT_TENANT)
    assert state is not None
    assert state.status is not RunStatus.CANCELLED, (
        "shutting a worker down cancelled somebody's run. Nobody asked for that, "
        "and `cancelled` is terminal — the run is gone rather than delayed"
    )
    assert not state.is_terminal


def test_the_completed_stages_survive_so_the_next_worker_resumes(claimed) -> None:  # type: ignore[no-untyped-def]
    """FR-022's reuse, applied to the shutdown path.

    Relinquishing would be a poor trade if it threw away the work already done.
    It does not: the stage that completed before the boundary kept its artifact,
    so the worker that picks the run up repeats at most the stage in flight.

    The signal arrives at the **second** boundary rather than the first, so a
    stage has actually completed by the time the worker lets go. Stopping at the
    first is the other real case — a worker signalled before it starts — and it
    is what every other test in this file exercises: nothing has been done, so
    there is nothing to preserve.
    """
    queue, blobs, store, run = claimed

    boundaries: list[int] = []

    def signalled_after_the_parse() -> bool:
        boundaries.append(1)
        return len(boundaries) > 1

    _execute(claimed, stopping=signalled_after_the_parse)

    assert list((store.root / "artifacts").rglob("*.json")), (
        "the interrupted run left no artifact, so the next worker starts over"
    )

    resumed = queue.claim(worker_id="w2", now=NOW, lease=DEFAULT_LEASE, max_attempts=3)
    assert resumed is not None
    result = execute_one(
        resumed,
        queue=queue,
        blobs=blobs,
        store=store,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        now=NOW,
    )

    assert result is not None
    assert result.outcome_for(Stage.PARSE).status is StageStatus.REUSED  # type: ignore[union-attr]
    finished = queue.get(run.run_id, DEFAULT_TENANT)
    assert finished is not None
    assert finished.status is RunStatus.SUCCEEDED


def test_a_worker_that_is_not_shutting_down_finishes_normally(claimed) -> None:  # type: ignore[no-untyped-def]
    """Guards all three above: a `release` on every path would satisfy them.

    With no shutdown and no cancellation the run must reach `succeeded` and name
    a `processing_id`, exactly as before this parameter existed.
    """
    queue, _blobs, _store, run = claimed

    result = _execute(claimed, stopping=lambda: False)

    assert result is not None
    state = queue.get(run.run_id, DEFAULT_TENANT)
    assert state is not None
    assert state.status is RunStatus.SUCCEEDED
    assert state.processing_id == result.processing_id


def test_a_cancelled_run_is_still_cancelled_and_not_released(claimed) -> None:  # type: ignore[no-untyped-def]
    """The two reasons to stop must not be confused for one another.

    A caller's cancellation is terminal; a shutdown is not. Both stop the
    pipeline at a boundary and both produce the same `PipelineResult`, so the
    only thing telling them apart is whether cancellation was actually
    requested — which is what this asserts.
    """
    queue, _blobs, _store, run = claimed
    queue.cancel(run.run_id, DEFAULT_TENANT, now=NOW)

    _execute(claimed, stopping=lambda: True)

    state = queue.get(run.run_id, DEFAULT_TENANT)
    assert state is not None
    assert state.status is RunStatus.CANCELLED, (
        "a run somebody cancelled was put back in the queue, so cancelling it "
        "delayed the work instead of stopping it"
    )
    assert state.processing_id is None


def test_the_worker_passes_its_shutdown_flag_down(tmp_path: Path) -> None:
    """The wiring, which the tests above would pass without.

    Every assertion here drives `execute_one` directly. If `Worker` stopped
    handing its own flag down, all of them would still pass and the deployed
    behaviour would be the one this file exists to remove.
    """
    import inspect

    from docdoc.runs import worker

    source = inspect.getsource(worker.Worker)

    assert "stopping=self._stopping.is_set" in source, (
        "the worker no longer passes its shutdown flag to `execute_one`, so a "
        "signalled worker runs the current document to completion and is killed "
        "mid-run — the exact behaviour FR-042 and FR-043 rule out"
    )
