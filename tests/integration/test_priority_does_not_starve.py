"""T141, FR-089 — an aged ordinary run outranks a newer urgent one.

`tests/unit/test_claim_order.py` asserts this against the claim *policy*. This
asserts it against the claim *query*, which is a different thing and the one that
ships: the ordering lives in a window function and an `ORDER BY`, and a clause
dropped or transposed there is invisible to a policy test.

The failure this prevents is the one every priority scheme has: a deployment with
a steady supply of urgent work in which an ordinary run is never claimed at all.
"Eventually" is not a bound, and a starvation bound that only exists in a
docstring is a bound nobody has.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from tests.infra import require_database

from docdoc.runs import migrations
from docdoc.runs.identity import DEFAULT_LEASE, new_run_id
from docdoc.runs.model import Priority
from docdoc.runs.postgres import PostgresRunQueue

pytestmark = pytest.mark.postgres

STARVATION = timedelta(hours=1)


@dataclass
class Spec:
    tenant_id: str = "acme"
    blob_id: str = "sha256:" + "a" * 64
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None
    priority: int = int(Priority.ORDINARY)
    callback_id: object = None


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


def _submit(queue: PostgresRunQueue, *, at: datetime, priority: Priority, tenant: str = "acme"):
    return queue.submit(
        Spec(tenant_id=tenant, priority=int(priority)),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=at,
        expires_at=at + timedelta(days=30),
    )


def _claim(queue: PostgresRunQueue, *, at: datetime):
    return queue.claim(
        worker_id="w1",
        now=at,
        lease=DEFAULT_LEASE,
        max_attempts=3,
        starvation=STARVATION,
    )


def test_urgent_is_claimed_before_ordinary_when_neither_has_waited(
    queue: PostgresRunQueue,
) -> None:
    """The ordinary case: within the bound, priority is what orders the queue."""
    now = datetime.now(UTC)
    ordinary = _submit(queue, at=now - timedelta(minutes=5), priority=Priority.ORDINARY)
    urgent = _submit(queue, at=now, priority=Priority.URGENT)

    claimed = _claim(queue, at=now)

    assert claimed is not None
    assert claimed.run_id == urgent.run_id, "priority orders work inside the bound"
    assert claimed.run_id != ordinary.run_id


def test_an_ordinary_run_past_the_bound_goes_first(queue: PostgresRunQueue) -> None:
    """**The requirement** (FR-089).

    The ordinary run has waited longer than the starvation bound, so it outranks
    a newer urgent one. Without this a deployment with steady urgent work has an
    ordinary run that is never claimed — and "eventually" stops being a promise
    anybody can act on.
    """
    now = datetime.now(UTC)
    starved = _submit(queue, at=now - STARVATION - timedelta(minutes=1), priority=Priority.ORDINARY)
    _submit(queue, at=now, priority=Priority.URGENT)

    claimed = _claim(queue, at=now)

    assert claimed is not None
    assert claimed.run_id == starved.run_id, (
        "an ordinary run past the starvation bound was passed over for a newer "
        "urgent one, which is the failure the bound exists to prevent"
    )


def test_two_starved_runs_are_still_ordered_by_priority(queue: PostgresRunQueue) -> None:
    """The bound promotes; it does not flatten.

    Once everything waiting is past the bound, priority orders them again — a
    scheme in which starvation erased priority entirely would make the whole
    feature stop working under exactly the load it was asked for.
    """
    now = datetime.now(UTC)
    _submit(queue, at=now - STARVATION - timedelta(minutes=2), priority=Priority.ORDINARY)
    urgent = _submit(queue, at=now - STARVATION - timedelta(minutes=1), priority=Priority.URGENT)

    claimed = _claim(queue, at=now)

    assert claimed is not None
    assert claimed.run_id == urgent.run_id


def test_with_no_priorities_the_order_is_creation_time(queue: PostgresRunQueue) -> None:
    """FR-091. A deployment that configures nothing claims exactly as Milestone 9
    claimed — which is what makes every capability here opt-in rather than a
    change of behaviour on upgrade."""
    now = datetime.now(UTC)
    first = _submit(queue, at=now - timedelta(minutes=3), priority=Priority.ORDINARY)
    _submit(queue, at=now - timedelta(minutes=2), priority=Priority.ORDINARY)
    _submit(queue, at=now - timedelta(minutes=1), priority=Priority.ORDINARY)

    claimed = _claim(queue, at=now)

    assert claimed is not None
    assert claimed.run_id == first.run_id
