"""SC-006 — a document that kills the worker stops after three, not after all.

The failure this bounds has a name and a shape. A document that terminates the
process handling it is redelivered when its lease lapses, terminates the next
worker, is redelivered again, and takes the pool one process at a time. Nothing
logs an error, because nothing survives to log one; the symptom is a fleet that
keeps restarting and a queue that never drains.

The attempt limit is the whole answer, and three is the number because **a
document that terminates three workers will terminate thirty** (research R9).
Retrying is for transient faults, and a poison document is not one.

Two properties, and the second is the one worth stating:

* the run comes to rest at `failed` with `error_class: "RunAbandonedError"`;
* it terminates **at most three** workers, not four and not an unbounded number.
  Counted on claims, because that is what "a worker was taken" means here.

`RunAbandonedError` is reserved for exactly this. A run that fails on
configuration — a withdrawn schema — fails once and terminally under FR-091, so
that this word keeps naming only the poison-document case and an operator who
reads it goes and looks at the document.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.runs.identity import DEFAULT_LEASE, new_run_id
from docdoc.runs.model import DEFAULT_TENANT, RunStatus

MAX_ATTEMPTS = 3
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


@dataclass
class Spec:
    blob_id: str = "sha256:" + "b" * 64
    tenant_id: str = DEFAULT_TENANT
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None


def _submit(queue: InMemoryRunQueue) -> Any:
    return queue.submit(
        Spec(),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=NOW,
        expires_at=NOW + timedelta(days=30),
    )


def _worker_dies_after_claiming(queue: InMemoryRunQueue, *, at: datetime) -> bool:
    """One worker: claim, then vanish.

    Nothing is finished and nothing is released, which is exactly what a process
    that was killed leaves behind — the distinguishing feature of this failure is
    that the worker gets no chance to record anything.

    Returns whether a run was claimed.
    """
    claimed = queue.claim(
        worker_id="doomed", now=at, lease=DEFAULT_LEASE, max_attempts=MAX_ATTEMPTS
    )
    return claimed is not None


def test_a_poison_run_comes_to_rest_at_failed_and_is_never_claimed_again() -> None:
    """SC-006, in the two halves it is stated in.

    Time advances past the lease each round, which is what redelivery *is*: the
    lease is the liveness signal, and a dead worker's run becomes eligible when
    its lease lapses. Advancing an instant rather than sleeping is what the
    `now`-as-a-parameter rule buys.
    """
    queue = InMemoryRunQueue()
    run = _submit(queue)

    claims = 0
    at = NOW
    # More rounds than the limit permits, deliberately: the assertion is that the
    # run stops being claimable, and a loop that stopped at three would be
    # asserting its own bound rather than the queue's.
    for _ in range(MAX_ATTEMPTS + 4):
        if _worker_dies_after_claiming(queue, at=at):
            claims += 1
        at = at + DEFAULT_LEASE + timedelta(seconds=1)

    assert claims == MAX_ATTEMPTS, (
        f"the run was handed to {claims} workers. Without a bound a poison "
        f"document takes the whole pool one process at a time, and the only "
        f"evidence is a queue that never drains"
    )

    final = queue.get(run.run_id, DEFAULT_TENANT)
    assert final is not None
    assert final.status is RunStatus.FAILED
    assert final.error_class == "RunAbandonedError", (
        f"the run rests at {final.error_class!r}. `RunAbandonedError` is the word "
        f"that sends an operator to look at the *document*, and it is reserved "
        f"for this case precisely so it keeps meaning that"
    )
    assert final.processing_id is None
    assert final.attempts == MAX_ATTEMPTS


def test_an_abandoned_run_is_terminal_and_no_transition_leaves_it() -> None:
    """FR-021 and FR-007 together: abandonment ends the run, it does not park it.

    A run that could still be claimed after abandonment would make the limit a
    delay rather than a bound.

    **Abandonment happens when a worker next comes looking**, not on a timer.
    There is no reaper process to deploy, monitor, and have fail silently — the
    transition is a clause in the claim (research R8) — so the run is still
    `running` with a lapsed lease until somebody asks, and this test asks.
    """
    queue = InMemoryRunQueue()
    run = _submit(queue)

    at = NOW
    for _ in range(MAX_ATTEMPTS):
        _worker_dies_after_claiming(queue, at=at)
        at = at + DEFAULT_LEASE + timedelta(seconds=1)

    refused = queue.claim(worker_id="w", now=at, lease=DEFAULT_LEASE, max_attempts=MAX_ATTEMPTS)

    assert refused is None
    assert queue.get(run.run_id, DEFAULT_TENANT).is_terminal  # type: ignore[union-attr]


def test_a_healthy_run_alongside_a_poison_one_still_completes() -> None:
    """The bound must not be a queue-wide stall.

    A poison document blocking the runs behind it would be a correct attempt
    limit producing an incorrect outcome: oldest-first ordering means the poison
    run is claimed first every time, so its abandonment has to actually let go.
    """
    queue = InMemoryRunQueue()
    poison = _submit(queue)
    healthy = queue.submit(
        Spec(blob_id="sha256:" + "c" * 64),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=NOW + timedelta(seconds=1),
        expires_at=NOW + timedelta(days=30),
    )

    at = NOW
    for _ in range(MAX_ATTEMPTS):
        _worker_dies_after_claiming(queue, at=at)
        at = at + DEFAULT_LEASE + timedelta(seconds=1)

    survivor = queue.claim(
        worker_id="healthy", now=at, lease=DEFAULT_LEASE, max_attempts=MAX_ATTEMPTS
    )

    assert survivor is not None, "the poison run blocked the queue behind it"
    assert survivor.run_id == healthy.run_id
    assert queue.get(poison.run_id, DEFAULT_TENANT).status is RunStatus.FAILED  # type: ignore[union-attr]


def test_the_limit_is_configurable_and_the_bound_follows_it() -> None:
    """Guards the constant: a test hard-coded to three proves nothing about two."""
    queue = InMemoryRunQueue()
    _submit(queue)

    claims = 0
    at = NOW
    for _ in range(6):
        if queue.claim(worker_id="w", now=at, lease=DEFAULT_LEASE, max_attempts=2) is not None:
            claims += 1
        at = at + DEFAULT_LEASE + timedelta(seconds=1)

    assert claims == 2
