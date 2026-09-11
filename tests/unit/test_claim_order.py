"""T094, T105, T139 — the four terms the claim orders by, and what they cost.

Milestone 9's FR-024 fixed claim order as ascending creation time and said, in
its own text, that this described an ordering rather than a discretion: *"with no
priority classes and no fairness policy in this milestone, nothing else is ever
unequal, so the qualifier described a discretion the design does not have."*

Something is now unequal. This file is the assertion that adding it **did not
change the default**: with no priorities set and one tenant, every new term is
constant and the order collapses to `created_at` (FR-091).

The three terms that were added, in the order the query applies them:

1. **starved** — past the bound a run outranks everything (FR-089), which is what
   makes a misconfigured priority ceiling a latency problem and never a liveness
   one;
2. **recent** — a *deficit*: the tenant served least in the last window goes
   first, so one tenant's backlog delays another by at most one claim per tenant
   with work rather than by the size of the backlog (FR-090).

   The first version of this term ranked each tenant's *queued* runs by position
   and looked like round-robin. It is not, and `test_a_thousand_runs_do_not_delay_one`
   is what found that: the rank recomputes over what is still waiting, so a
   tenant with a backlog always has another rank-0 run and it is always the older
   one — 201 claims in a row. Research R10 is corrected;
3. **priority** — within a tenant (FR-088).

Run against the in-memory queue, which implements the same four terms. That is
the point of having it: the policy is what is worth testing and it needs no
database (research R10).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

from docdoc.runs.model import Priority, Run, RunStatus
from tests.fixtures.run_queue import InMemoryRunQueue

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
LEASE = timedelta(seconds=90)
HOUR = timedelta(hours=1)


def _queued(
    queue: InMemoryRunQueue,
    *,
    tenant_id: str = "acme",
    age_seconds: int = 0,
    priority: Priority = Priority.ORDINARY,
) -> Run:
    created = NOW - timedelta(seconds=age_seconds)
    run = Run(
        run_id=uuid4(),
        tenant_id=tenant_id,
        blob_id="sha256:" + "ab" * 32,
        schema_identity="invoice@1",
        status=RunStatus.QUEUED,
        priority=priority,
        created_at=created,
        updated_at=created,
        expires_at=NOW + timedelta(days=30),
    )
    queue._runs[run.run_id] = run
    return run


def _claim(queue: InMemoryRunQueue, *, starvation: timedelta = HOUR) -> Run | None:
    return queue.claim(worker_id="w1", now=NOW, lease=LEASE, max_attempts=3, starvation=starvation)


class TestTheDefaultIsMilestone9sBehaviour:
    """FR-091 — the old order is the default, not a special case of the new one.

    **For one tenant.** A code review pointed out that FR-091 originally said "no
    priorities and no fairness policy", which assumed fairness was configurable.
    It is not: FR-090 requires a bound on how long one tenant's backlog may delay
    another, and a switch that turned that off would restore the starvation
    FR-090 forbids. So the guarantee is the one below, and the cost -- a
    multi-tenant Milestone 9 deployment sees per-tenant fairness in place of
    strict global FIFO on upgrade -- is stated in the spec rather than implied.
    """

    def test_one_tenant_no_priorities_is_ascending_creation_time(self) -> None:
        queue = InMemoryRunQueue()
        newest = _queued(queue, age_seconds=10)
        oldest = _queued(queue, age_seconds=300)
        middle = _queued(queue, age_seconds=100)

        order = [_claim(queue), _claim(queue), _claim(queue)]

        assert [run.run_id for run in order if run] == [
            oldest.run_id,
            middle.run_id,
            newest.run_id,
        ]

    def test_several_tenants_do_not_get_global_fifo_and_that_is_the_point(self) -> None:
        """The stated exception to FR-101, asserted rather than left implicit."""
        queue = InMemoryRunQueue()
        _queued(queue, tenant_id="bulk", age_seconds=300)
        _queued(queue, tenant_id="bulk", age_seconds=290)
        acme = _queued(queue, tenant_id="acme", age_seconds=10)

        first, second = _claim(queue), _claim(queue)

        assert first is not None
        assert second is not None
        assert second.run_id == acme.run_id, (
            "with several tenants the claim is not global FIFO, deliberately: "
            "FR-090's bound cannot be conditional without reintroducing the "
            "starvation it forbids (FR-091, and FR-101's one stated exception)."
        )

    def test_the_starvation_bound_changes_nothing_when_nothing_is_starved(self) -> None:
        queue = InMemoryRunQueue()
        oldest = _queued(queue, age_seconds=60)
        _queued(queue, age_seconds=10)

        claimed = _claim(queue, starvation=timedelta(days=1))

        assert claimed is not None
        assert claimed.run_id == oldest.run_id


class TestPriorityWithinATenant:
    def test_urgent_is_claimed_before_ordinary(self) -> None:
        queue = InMemoryRunQueue()
        _queued(queue, age_seconds=300)
        urgent = _queued(queue, age_seconds=1, priority=Priority.URGENT)

        claimed = _claim(queue)

        assert claimed is not None
        assert claimed.run_id == urgent.run_id

    def test_priority_does_not_reorder_across_tenants_by_itself(self) -> None:
        """The deficit is applied before priority, which is what bounds the delay.

        A tenant cannot buy its way past another tenant's oldest run by marking
        everything urgent: within each tenant the urgent one comes first, and the
        two tenants' firsts still alternate.
        """
        queue = InMemoryRunQueue()
        bulk = [
            _queued(queue, tenant_id="bulk", age_seconds=300 - index, priority=Priority.URGENT)
            for index in range(3)
        ]
        acme = _queued(queue, tenant_id="acme", age_seconds=10)

        first, second = _claim(queue), _claim(queue)

        assert first is not None
        assert second is not None
        claimed = {first.tenant_id, second.tenant_id}
        assert claimed == {"bulk", "acme"}, (
            "one tenant's urgent backlog took two claims in a row. The claim "
            "prefers the tenant served least recently, so that cannot happen "
            "(FR-090)."
        )
        assert first.run_id == bulk[0].run_id
        assert second.run_id == acme.run_id


class TestFairnessIsABound:
    """FR-090, SC-010 — proportional to tenants, not to the backlog."""

    def test_a_thousand_runs_do_not_delay_one(self) -> None:
        queue = InMemoryRunQueue()
        for index in range(200):
            _queued(queue, tenant_id="bulk", age_seconds=1000 - index)
        acme = _queued(queue, tenant_id="acme", age_seconds=1)

        claims = 0
        while (claimed := _claim(queue)) is not None:
            claims += 1
            if claimed.run_id == acme.run_id:
                break
            queue.finish(
                claimed.run_id,
                __import__("docdoc.runs.model", fromlist=["RunOutcome"]).RunOutcome(
                    status=RunStatus.CANCELLED
                ),
                now=NOW,
            )

        assert claims <= 2, (
            f"the second tenant's run waited {claims} claims behind a backlog of "
            "200. The bound is the number of tenants with work, not the size of "
            "anyone's queue (SC-010)."
        )


class TestStarvationOutranksEverything:
    """FR-089 — so a wrong ceiling costs latency and never liveness."""

    def test_an_aged_ordinary_run_beats_a_new_urgent_one(self) -> None:
        queue = InMemoryRunQueue()
        aged = _queued(queue, age_seconds=7200)
        _queued(queue, age_seconds=1, priority=Priority.URGENT)

        claimed = _claim(queue, starvation=HOUR)

        assert claimed is not None
        assert claimed.run_id == aged.run_id

    def test_it_beats_another_tenants_first_run_too(self) -> None:
        """Starvation is checked before the deficit, which is the point of it."""
        queue = InMemoryRunQueue()
        starved = _queued(queue, tenant_id="bulk", age_seconds=7200)
        _queued(queue, tenant_id="acme", age_seconds=1, priority=Priority.URGENT)

        claimed = _claim(queue, starvation=HOUR)

        assert claimed is not None
        assert claimed.run_id == starved.run_id


class TestPriorityChangesNoResult:
    """FR-093 — it affects when a run is claimed and nothing else."""

    def test_it_is_not_passed_below_the_runs_layer(self) -> None:
        """Asserted structurally: the pipeline's signature does not name it."""
        import inspect

        from docdoc.pipeline import run as pipeline_run

        assert "priority" not in inspect.signature(pipeline_run).parameters
