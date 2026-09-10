"""T099a, T104 — the two claims that only a real database can settle.

**A limit is not per-process** (FR-051). The in-memory limiter cannot show this:
it *is* one process. Two `PostgresLimiter` instances against one database stand
in for two API replicas, and if they did not share a budget then every limit a
deployment configured would be multiplied by its replica count — silently, and in
the direction that makes the limit useless.

**Fairness is a bound** (FR-090, SC-010). `tests/unit/test_claim_order.py` asserts
the ordering policy against the fake; this asserts that the SQL implements the
same policy. The two are different claims and the first has already been wrong
once: the original design ranked each tenant's queued runs by position, which
looks like round-robin and is not.
"""

from __future__ import annotations

from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from tests.infra import require_database

from docdoc.runs.identity import DEFAULT_STARVATION
from docdoc.runs.identity import now as clock
from docdoc.runs.limits import LimitPolicy, PostgresLimiter
from docdoc.runs.model import RunOutcome, RunStatus
from docdoc.runs.postgres import PostgresRunQueue

if TYPE_CHECKING:
    from datetime import datetime

pytestmark = pytest.mark.postgres

LEASE = timedelta(seconds=90)


class _Spec:
    """The minimum `submit` needs, without importing the API's request model."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.blob_id = "sha256:" + "ab" * 32
        self.schema_identity = "invoice@1"
        self.request_id = None
        self.idempotency_key = None


@pytest.fixture
def dsn() -> str:
    return require_database()


@pytest.fixture
def queue(dsn: str) -> PostgresRunQueue:
    import psycopg

    from docdoc.runs import migrations

    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=clock())
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM runs")
            cursor.execute("DELETE FROM limit_counters")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


def _submit(queue: PostgresRunQueue, tenant_id: str, at: datetime) -> object:
    return queue.submit(
        _Spec(tenant_id),  # type: ignore[arg-type]
        run_id=uuid4(),
        now=at,
        expires_at=at + timedelta(days=30),
    )


class TestALimitIsNotPerProcess:
    """FR-051 — what the in-memory limiter cannot show, being one process."""

    def test_two_replicas_share_one_budget(self, queue: PostgresRunQueue) -> None:
        policy = LimitPolicy(submissions_per_minute=2)
        first = PostgresLimiter(execute=queue._execute, policy=policy)
        second = PostgresLimiter(execute=queue._execute, policy=policy)
        at = clock()

        assert first.check(tenant_id="acme", now=at).allowed
        first.record_submission(tenant_id="acme", now=at)
        assert second.check(tenant_id="acme", now=at).allowed
        second.record_submission(tenant_id="acme", now=at)

        assert first.check(tenant_id="acme", now=at).allowed is False, (
            "the second replica's submissions did not count against the first's "
            "budget. An in-process counter multiplies every configured limit by "
            "the replica count, silently (FR-051)."
        )
        assert second.check(tenant_id="acme", now=at).allowed is False

    def test_concurrency_is_read_from_runs_not_accumulated(self, queue: PostgresRunQueue) -> None:
        """So a finished run frees the slot without anybody decrementing."""
        limiter = PostgresLimiter(execute=queue._execute, policy=LimitPolicy(concurrent_runs=1))
        at = clock()
        run = _submit(queue, "acme", at)

        assert limiter.check(tenant_id="acme", now=at).allowed is False

        queue.finish(run.run_id, RunOutcome(status=RunStatus.CANCELLED), now=at)  # type: ignore[attr-defined]

        assert limiter.check(tenant_id="acme", now=at).allowed


class TestFairnessIsABoundInSQLToo:
    """SC-010 — the same policy `test_claim_order.py` asserts against the fake."""

    def test_a_backlog_does_not_delay_another_tenant(self, queue: PostgresRunQueue) -> None:
        at = clock() - timedelta(minutes=1)
        for index in range(50):
            _submit(queue, "bulk", at + timedelta(seconds=index))
        acme = _submit(queue, "acme", at + timedelta(seconds=100))

        claims = 0
        while True:
            claimed = queue.claim(
                worker_id="w1",
                now=clock(),
                lease=LEASE,
                max_attempts=3,
                starvation=DEFAULT_STARVATION,
            )
            assert claimed is not None
            claims += 1
            if claimed.run_id == acme.run_id:  # type: ignore[attr-defined]
                break
            queue.finish(claimed.run_id, RunOutcome(status=RunStatus.CANCELLED), now=clock())
            assert claims < 10, "the SQL is not alternating between tenants"

        assert claims <= 2, (
            f"the second tenant waited {claims} claims behind a backlog of 50. "
            "The bound is the number of tenants with work (SC-010)."
        )

    def test_one_tenant_still_gets_creation_order(self, queue: PostgresRunQueue) -> None:
        """FR-091 — with one tenant the fairness term drops out entirely."""
        at = clock() - timedelta(minutes=5)
        first = _submit(queue, "acme", at)
        second = _submit(queue, "acme", at + timedelta(seconds=1))

        claimed = queue.claim(
            worker_id="w1",
            now=clock(),
            lease=LEASE,
            max_attempts=3,
            starvation=DEFAULT_STARVATION,
        )

        assert claimed is not None
        assert claimed.run_id == first.run_id  # type: ignore[attr-defined]
        assert claimed.run_id != second.run_id  # type: ignore[attr-defined]
