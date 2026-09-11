"""T104a — the cost of the fairness term, measured rather than assumed.

plan.md's performance goals say the claim query's ordering is "measured rather
than assumed", and this is that measurement. Research R10 accepts a real cost:
the fairness term is a `GROUP BY` over recently-updated rows, which the partial
index on non-terminal rows does not serve, and the window bounds it to ten
minutes of a deployment's finished runs rather than to the table.

**A guardrail, not a benchmark.** The threshold is loose on purpose, so it fails
on a complexity mistake — an ordering that scans the whole table, a fairness term
that grows with the backlog — rather than on a slow machine.

**This docstring used to say claim latency "must be flat in queue depth". It is
not flat, it is not meant to be, and the test never checked it.** Measured
against a real database — one machine, best of three, indicative rather than a
contract:

| queue depth | claim |
|---|---|
| 100 | ~40 ms |
| 1 000 | ~40 ms |
| 10 000 | ~50 ms |
| 50 000 | ~107 ms |

That growth is R10's accepted cost: the ordering sorts the *eligible* set, which
`EXPLAIN` confirms as an `external merge` spilling a few megabytes at the top
depth. plan.md records it as "bounded by queue depth rather than table size, and
measured rather than assumed" — bounded by queue depth is exactly what this is.
So the behaviour was right and the prose overreached, which is the worse of the
two ways to be wrong: a reader trusts the claim and the test does not check it.

**And the ceiling alone was not catching what it claimed to.** At 10 000 the
fixed cost of a round trip (~35 ms) dominates and the sort is a couple of
milliseconds, so a sort that had gone *quadratic* would land near 235 ms — well
under a second, and green. The depth is therefore carried to 50 000, where the
same regression is seconds rather than milliseconds. That is the number the
budget is now defending, and the reason there is a fourth row above.

    uv run pytest tests/perf -m perf
"""

from __future__ import annotations

import time
from datetime import timedelta
from uuid import uuid4

import pytest

from docdoc.runs.identity import DEFAULT_STARVATION
from docdoc.runs.identity import now as clock
from docdoc.runs.model import RunOutcome, RunStatus
from docdoc.runs.postgres import PostgresRunQueue
from tests.infra import require_database

pytestmark = [pytest.mark.perf, pytest.mark.postgres]

LEASE = timedelta(seconds=90)

#: Per claim, at every depth below. Generous: a claim is one round trip and the
#: query sorts an eligible set, so anything under a second is "the plan did not
#: collapse". A regression that mattered would show as seconds, not milliseconds.
#:
#: The headroom is real at the largest depth and that is the point: 107 ms
#: measured against 1 000 ms allowed, so this fails on a complexity mistake and
#: not on a machine that is nine times slower than the one it was measured on.
BUDGET_SECONDS = 1.0

#: The depths, and the last one is load-bearing.
#:
#: At 10 000 a round trip dominates and the sort is milliseconds, so a sort gone
#: quadratic still lands well inside the budget. At 50 000 it does not — the same
#: regression is seconds. A guardrail whose largest case cannot distinguish
#: `O(n log n)` from `O(n²)` is measuring the round trip.
DEPTHS = [100, 1_000, 10_000, 50_000]


class _Spec:
    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.blob_id = "sha256:" + "ab" * 32
        self.schema_identity = "invoice@1"
        self.request_id = None
        self.idempotency_key = None


def _queue(dsn: str) -> PostgresRunQueue:
    import psycopg

    from docdoc.runs import migrations

    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=clock())
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM runs")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


def _seed(queue: PostgresRunQueue, depth: int, at: object) -> None:
    """`depth` queued runs across ten tenants, in one round trip."""
    rows = [
        (
            uuid4(),
            f"tenant-{index % 10}",
            "sha256:" + "ab" * 32,
            "invoice@1",
            "queued",
            at + timedelta(milliseconds=index),
            at + timedelta(milliseconds=index),
            at + timedelta(days=30),
        )
        for index in range(depth)
    ]
    # Chunked at 5 000 rows, which is 40 000 parameters. Postgres caps a
    # statement at 65 535, and the first version of this helper found that out
    # by sending 80 000.
    for start in range(0, len(rows), 5_000):
        chunk = rows[start : start + 5_000]
        values = ",".join(["(%s, %s, %s, %s, %s, %s, %s, %s)"] * len(chunk))
        queue._execute(
            "INSERT INTO runs (run_id, tenant_id, blob_id, schema_identity, status, "
            f"created_at, updated_at, expires_at) VALUES {values}",
            tuple(field for row in chunk for field in row),
        )


@pytest.mark.parametrize("depth", DEPTHS)
def test_claim_latency_stays_inside_its_budget(depth: int) -> None:
    """One claim, at each depth, against the ceiling the module docstring argues."""
    queue = _queue(require_database())
    at = clock() - timedelta(hours=2)

    # Seeded with one statement rather than `depth` calls to `submit`. The first
    # version did the latter and spent 190 seconds inserting to measure a claim
    # that takes milliseconds -- a perf test whose cost is its own setup gets
    # marked slow and then gets skipped, which is how a guardrail stops guarding.
    _seed(queue, depth, at)

    started = time.perf_counter()
    claimed = queue.claim(
        worker_id="w1",
        now=clock(),
        lease=LEASE,
        max_attempts=3,
        starvation=DEFAULT_STARVATION,
    )
    elapsed = time.perf_counter() - started

    assert claimed is not None
    assert elapsed < BUDGET_SECONDS, (
        f"one claim took {elapsed:.3f}s at a queue depth of {depth}, against a "
        f"budget of {BUDGET_SECONDS:.1f}s.\n"
        "Growing with depth is expected — the ordering sorts the eligible set and "
        "R10 accepted that. Growing by *this much* is not: at this depth the "
        "measured cost is around a tenth of the budget, so exceeding it means the "
        "sort or the fairness GROUP BY has changed complexity, or is now scanning "
        "the table rather than the queue."
    )

    queue.finish(claimed.run_id, RunOutcome(status=RunStatus.CANCELLED), now=clock())
