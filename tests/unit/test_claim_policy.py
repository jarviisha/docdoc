"""Claim policy, at arbitrary instants, with no database and no sleeping.

Every question here is about a rule rather than about SQL: which run is taken
first, when a lapsed lease makes one eligible again, and what bounds redelivery.
`RunQueue` takes `now` as a parameter precisely so these can be asked at any
instant (FR-072), which is what makes a lease-expiry test take microseconds
instead of ninety seconds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from docdoc.runs.identity import new_run_id
from docdoc.runs.model import RunStatus
from tests.fixtures.run_queue import InMemoryRunQueue

T0 = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
LEASE = timedelta(seconds=90)


@dataclass
class Spec:
    tenant_id: str = "default"
    blob_id: str = "sha256:blob"
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None


def _submit(queue: InMemoryRunQueue, *, at: datetime, **kwargs: object):
    """Return the id of the run that *exists*, which is not always the one offered.

    Under an idempotency key a repeat submission returns the original run, so the
    generated id is discarded. Returning the generated one instead would make the
    idempotency tests below pass while asserting nothing.
    """
    run = queue.submit(
        Spec(**kwargs),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=at,
        expires_at=at + timedelta(days=30),
    )
    return run.run_id


def test_the_oldest_eligible_run_is_claimed_first() -> None:
    """FR-024. With no priority classes, creation order is the whole ordering."""
    queue = InMemoryRunQueue()
    first = _submit(queue, at=T0)
    _submit(queue, at=T0 + timedelta(seconds=1))
    _submit(queue, at=T0 + timedelta(seconds=2))

    claimed = queue.claim(
        worker_id="w1", now=T0 + timedelta(minutes=1), lease=LEASE, max_attempts=3
    )

    assert claimed is not None
    assert claimed.run_id == first


def test_an_empty_queue_claims_nothing_rather_than_waiting() -> None:
    queue = InMemoryRunQueue()
    assert queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3) is None


def test_a_claimed_run_is_invisible_to_a_second_worker_while_its_lease_holds() -> None:
    queue = InMemoryRunQueue()
    _submit(queue, at=T0)

    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)
    second = queue.claim(
        worker_id="w2", now=T0 + timedelta(seconds=30), lease=LEASE, max_attempts=3
    )

    assert second is None, "two workers must never hold one run (FR-016)"


def test_a_lapsed_lease_makes_a_run_claimable_again() -> None:
    """Expiry is a clause in the claim, not a reaper process (R8).

    A reaper is a component to deploy, monitor, and have fail silently; the `OR`
    clause cannot fail separately from the claim it is part of.
    """
    queue = InMemoryRunQueue()
    run_id = _submit(queue, at=T0)
    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)

    after = T0 + LEASE + timedelta(seconds=1)
    redelivered = queue.claim(worker_id="w2", now=after, lease=LEASE, max_attempts=3)

    assert redelivered is not None
    assert redelivered.run_id == run_id
    assert redelivered.worker_id == "w2"
    assert redelivered.attempts == 2, "redelivery consumes an attempt"


def test_a_lease_one_second_from_lapsing_is_still_held() -> None:
    queue = InMemoryRunQueue()
    _submit(queue, at=T0)
    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)

    assert queue.claim(worker_id="w2", now=T0 + LEASE, lease=LEASE, max_attempts=3) is None


def test_heartbeat_extends_the_lease_and_keeps_the_run_owned() -> None:
    queue = InMemoryRunQueue()
    run_id = _submit(queue, at=T0)
    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)

    at = T0 + timedelta(seconds=60)
    assert queue.heartbeat(run_id, now=at, lease=LEASE) is True

    # The original lease would have lapsed by now; the extended one has not.
    assert (
        queue.claim(
            worker_id="w2", now=T0 + LEASE + timedelta(seconds=1), lease=LEASE, max_attempts=3
        )
        is None
    )


def test_a_superseded_worker_learns_it_lost_the_run() -> None:
    """`heartbeat` returning False is how a worker knows to stop.

    Without it, a worker whose lease lapsed would finish and write a result for
    work another worker is already redoing.
    """
    queue = InMemoryRunQueue()
    run_id = _submit(queue, at=T0)
    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)

    lapsed = T0 + LEASE + timedelta(seconds=1)
    queue.claim(worker_id="w2", now=lapsed, lease=LEASE, max_attempts=3)

    assert queue.heartbeat(run_id, now=lapsed, lease=LEASE) is True, (
        "w2 now holds it, and its heartbeat must work"
    )


def test_release_re_queues_immediately_rather_than_waiting_out_the_lease() -> None:
    """FR-043: what a worker does on SIGTERM, so a rolling restart costs nothing."""
    queue = InMemoryRunQueue()
    run_id = _submit(queue, at=T0)
    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)

    queue.release(run_id, now=T0 + timedelta(seconds=5))

    claimed = queue.claim(
        worker_id="w2", now=T0 + timedelta(seconds=6), lease=LEASE, max_attempts=3
    )
    assert claimed is not None
    assert claimed.run_id == run_id


def test_a_run_that_keeps_losing_its_worker_is_abandoned_not_redelivered_forever() -> None:
    """FR-021 and SC-006: the bound that stops a poison document.

    Without it, a document that terminates a worker is handed to the next one
    indefinitely and kills the pool one process at a time.
    """
    queue = InMemoryRunQueue()
    run_id = _submit(queue, at=T0)

    at = T0
    for _ in range(3):
        assert queue.claim(worker_id="w", now=at, lease=LEASE, max_attempts=3) is not None
        at += LEASE + timedelta(seconds=1)

    assert queue.claim(worker_id="w", now=at, lease=LEASE, max_attempts=3) is None

    run = queue.get(run_id, "default")
    assert run is not None
    assert run.status is RunStatus.FAILED
    assert run.error_class == "RunAbandonedError"
    assert run.attempts == 3


# -- idempotency ---------------------------------------------------------------


def test_the_same_key_twice_under_one_tenant_produces_one_run() -> None:
    """FR-011, and the first half of SC-016."""
    queue = InMemoryRunQueue()
    first = _submit(queue, at=T0, idempotency_key="k1")
    second = _submit(queue, at=T0, idempotency_key="k1")

    assert second == first, "the second submission must return the original run"


def test_the_same_key_under_two_tenants_produces_two_runs() -> None:
    """SC-016's second half: the key is scoped, not global."""
    queue = InMemoryRunQueue()
    a = _submit(queue, at=T0, tenant_id="acme", idempotency_key="k1")
    b = _submit(queue, at=T0, tenant_id="other", idempotency_key="k1")

    assert a != b
    assert queue.get(a, "acme") is not None
    assert queue.get(b, "other") is not None
