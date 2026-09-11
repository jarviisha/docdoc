"""T173, SC-025, SC-024 — four process types, four services, one bounded tick.

This milestone added retention, delivery, and counter expiry, and every one of
them is the kind of work that usually arrives as a scheduler. None of them did.
The claim is that the topology did not move, and a topology claim is only worth
having if something counts.

**Four process types**: the API, the worker, the database, and the object store.
`docdoc sweep`, `docdoc erase`, and `docdoc credential` are invocations, not
process types — an operator runs them and they exit.

**No thread, no subprocess, no event loop** in the maintenance tick. Milestone
9's FR-025 forbids them in the worker, and its reason reaches here unchanged: a
GIL-holding stage must not be able to delay a sibling's heartbeat into losing a
lease it still holds.
"""

from __future__ import annotations

import inspect
import pathlib
import re
from datetime import UTC, datetime, timedelta

import pytest

from docdoc.runs import identity, maintenance, retention

COMPOSE = pathlib.Path("packaging/docker/compose.yml")
AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


# -- SC-025: four services, and no fifth --------------------------------------


#: `createbucket` is not a fifth process type and is excluded by name rather than
#: by the regex missing it. It is a one-shot `mc mb` that creates the object
#: store's bucket and exits, so nothing is deployed, monitored, or able to fail
#: silently — which is the whole of what FR-116's prohibition is about. It
#: predates this milestone.
_ONE_SHOT = {"createbucket"}


def _services() -> set[str]:
    """The service names, read from the `services:` block only.

    Scoped to that block because the file's top-level `volumes:` keys sit at the
    same indent, and a check that counted `pgdata` as a process would be wrong in
    the direction that fails loudly — which is the harmless direction, but it
    would also have hidden a real fifth service behind a message about a volume.
    """
    text = COMPOSE.read_text(encoding="utf-8")
    block = text.split("\nservices:\n", 1)[1].split("\nvolumes:\n", 1)[0]
    return set(re.findall(r"^  ([a-z][a-z0-9_-]*):$", block, re.MULTILINE)) - _ONE_SHOT


def test_the_composition_has_exactly_four_services() -> None:
    """A collector, if an operator wants one, is theirs.

    Milestone 10 adds an OTLP exporter and does **not** add a collector to ship
    it to. That is the constitutional scope constraint holding: the composition
    demonstrates docdoc, and a collector is somebody else's infrastructure.
    """
    assert _services() == {"api", "worker", "postgres", "minio"}, (
        f"the composition has {sorted(_services())}. SC-025 asserts four, and "
        f"every capability this milestone added was built into a loop that "
        f"already runs rather than into a process to deploy and monitor"
    )


def test_no_scheduler_appears_in_the_composition() -> None:
    """The failure this is written against is a `cron` sidecar arriving quietly.

    Word boundaries, because `heartbeat` contains `beat` and the prose in this
    file talks about heartbeats at length. A guard that fired on a comment would
    be a guard somebody deletes.
    """
    text = COMPOSE.read_text(encoding="utf-8").lower()

    for word in ("cron", "scheduler", "celery", "beat", "rabbitmq", "redis", "sidekiq"):
        assert not re.search(rf"\b{word}\b", text), f"the composition names {word!r}"


def test_the_two_docdoc_services_run_the_two_commands() -> None:
    """`docdoc-api` and `docdoc worker`, from one image. Not a third entry point."""
    text = COMPOSE.read_text(encoding="utf-8")

    assert 'command: ["docdoc", "worker"' in text
    assert "docdoc sweep" not in text, (
        "the sweep runs inside the worker's tick. A compose service for it would "
        "be the fifth process type FR-116 forbids, wearing a different name"
    )
    assert "docdoc erase" not in text


# -- FR-114: no thread, no subprocess, no event loop --------------------------


def test_the_tick_starts_nothing() -> None:
    """Read from the **imports**, not from the text.

    What is asserted is an absence, and there is no behaviour to observe: a tick
    that spawned a thread would work, and would only be visible when a heartbeat
    missed under load.

    An AST walk rather than a substring search, because the module's own
    docstring says "no thread, no subprocess, no event loop" — a text search
    fires on the sentence explaining the rule, which is a guard that punishes
    documenting itself.
    """
    import ast

    tree = ast.parse(inspect.getsource(maintenance))
    imported = {
        alias.name.split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    } | {
        (node.module or "").split(".")[0]
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
    }

    forbidden = {"threading", "subprocess", "asyncio", "multiprocessing", "concurrent"}
    assert not (imported & forbidden), (
        f"`maintenance` imports {sorted(imported & forbidden)}. FR-025's reason "
        f"reaches here unchanged: a GIL-holding stage must not delay a sibling's "
        f"heartbeat into losing a lease it still holds"
    )


# -- SC-024: the tick is bounded ----------------------------------------------


class _Nothing:
    def delete(self, identity: str) -> bool:
        return False

    def delete_prefix(self, *, allow_store_root: bool = False) -> int:
        return 0


class _EndlessQueue:
    """A queue that always has more to sweep, so only the budget can stop it."""

    def __init__(self) -> None:
        self.calls = 0

    def expiring(self, *, now, limit, exclude=()):
        from docdoc.runs.model import Run, RunStatus

        self.calls += 1
        return tuple(
            Run(
                run_id=__import__("uuid").uuid4(),
                tenant_id="acme",
                blob_id="sha256:" + "a" * 64,
                schema_identity="invoice@1",
                status=RunStatus.SUCCEEDED,
                processing_id="sha256:" + "b" * 64,
                created_at=now,
                updated_at=now,
                expires_at=now,
            )
            for _ in range(limit)
        )

    def retained_ids_for(self, tenant_id, *, excluding):
        return (frozenset(), frozenset())

    def entomb(self, runs, *, now, policy):
        return len(list(runs))


class _Clock:
    """A monotonic source the test advances, so nothing sleeps."""

    def __init__(self, step_ms: float) -> None:
        self.now = 0.0
        self.step = step_ms

    def __call__(self) -> float:
        self.now += self.step
        return self.now


def test_a_tick_stops_at_its_budget() -> None:
    """**SC-024.** The work is endless; the tick is not."""
    queue = _EndlessQueue()

    report = maintenance.tick(
        maintenance.MaintenanceDeps(
            queue=queue,
            stores_for=lambda _: retention.Stores(artifacts=_Nothing(), blobs=_Nothing()),
        ),
        now=AT,
        retention_period=timedelta(days=1),
        batch=10,
        budget_ms=100,
        monotonic_ms=_Clock(step_ms=30),
    )

    assert report.exhausted, "the budget is what stopped it"
    # Four reads of the clock at 30ms each crosses 100ms, so the loop runs a
    # small, bounded number of times rather than until the queue empties.
    assert queue.calls <= 5


def test_a_tick_with_nothing_configured_does_nothing() -> None:
    """The default. No retention period, no deliverer, no limiter."""
    queue = _EndlessQueue()

    report = maintenance.tick(
        maintenance.MaintenanceDeps(
            queue=queue,
            stores_for=lambda _: retention.Stores(artifacts=_Nothing(), blobs=_Nothing()),
        ),
        now=AT,
        retention_period=None,
        batch=10,
    )

    assert queue.calls == 0
    assert report.swept.runs == 0
    assert report.attempted == 0


def test_the_ticker_skips_until_the_interval_has_passed() -> None:
    """A busy worker claiming continuously must not sweep on every iteration."""
    ticker = maintenance.Ticker(interval_seconds=60)

    assert ticker.due(AT), "the first call is always due"
    ticker.ran(AT)

    assert not ticker.due(AT + timedelta(seconds=59))
    assert ticker.due(AT + timedelta(seconds=60))


def test_deliveries_are_attempted_before_the_sweep() -> None:
    """The ordering, asserted rather than left to the code's line order.

    A delivery is a promise made to somebody outside the deployment; a sweep is
    idempotent housekeeping. When the budget runs out, the housekeeping is what
    should be the thing that did not happen.
    """
    order: list[str] = []

    class _Deliverer:
        def due(self, *, at, limit):
            order.append("delivery")
            return ()

    class _Queue(_EndlessQueue):
        def expiring(self, *, now, limit, exclude=()):
            order.append("sweep")
            return ()

    maintenance.tick(
        maintenance.MaintenanceDeps(
            queue=_Queue(),
            stores_for=lambda _: retention.Stores(artifacts=_Nothing(), blobs=_Nothing()),
            deliverer=_Deliverer(),
        ),
        now=AT,
        retention_period=timedelta(days=1),
        batch=10,
    )

    assert order == ["delivery", "sweep"]


def test_a_failing_step_does_not_stop_the_next_one_from_being_tried() -> None:
    """A tick never raises into the claim loop: the runs are what a deployment is
    paid for, and a maintenance failure is a degradation to log."""
    from docdoc.runs.errors import RunStateUnavailableError

    class _BrokenDeliverer:
        def due(self, *, at, limit):
            raise RunStateUnavailableError("gone")

    report = maintenance.tick(
        maintenance.MaintenanceDeps(
            queue=_EndlessQueue(),
            stores_for=lambda _: retention.Stores(artifacts=_Nothing(), blobs=_Nothing()),
            deliverer=_BrokenDeliverer(),
        ),
        now=AT,
        retention_period=None,
        batch=10,
    )

    assert report.attempted == 0


def test_the_counter_expiry_step_runs_and_is_best_effort() -> None:
    """T175b. A counter table that only grows is the failure this feature exists
    to prevent, arriving by the back door."""
    seen: list[object] = []

    class _Limiter:
        def expire_windows(self, *, now, keep):
            seen.append(keep)
            return 3

    maintenance.tick(
        maintenance.MaintenanceDeps(
            queue=_EndlessQueue(),
            stores_for=lambda _: retention.Stores(artifacts=_Nothing(), blobs=_Nothing()),
            limiter=_Limiter(),
        ),
        now=AT,
        retention_period=None,
        batch=10,
    )

    from docdoc.runs.identity import LIMIT_RETENTION, LIMIT_WINDOWS

    assert seen == [LIMIT_RETENTION]
    assert max(LIMIT_WINDOWS.values()) < LIMIT_RETENTION, (
        "removing a row at exactly one window would race the check reading the "
        "window that just ended"
    )


def test_a_limiter_with_no_expiry_is_not_an_error() -> None:
    """`NullLimiter` counts nothing, so it has nothing to expire."""
    from docdoc.runs.limits import NullLimiter

    maintenance.tick(
        maintenance.MaintenanceDeps(
            queue=_EndlessQueue(),
            stores_for=lambda _: retention.Stores(artifacts=_Nothing(), blobs=_Nothing()),
            limiter=NullLimiter(),
        ),
        now=AT,
        retention_period=None,
        batch=10,
    )


@pytest.mark.parametrize("name", ["api", "worker"])
def test_both_docdoc_services_run_the_same_image(name: str) -> None:
    """One image, two entry points. A second image would be a second thing to
    version, and the two would eventually disagree about a schema."""
    text = COMPOSE.read_text(encoding="utf-8")

    assert f"  {name}:" in text
    assert text.count("build: &docdoc_build") + text.count("build: *docdoc_build") >= 2


# -- T183: SC-024's second half -----------------------------------------------
#
# "A maintenance tick delays a claim by at most the budget plus one delivery
# timeout, **with zero leases lost**." The budget half is asserted above. The
# lease half was prose only, and it is the half that costs something when it is
# wrong: a tick that overran would let a live run's lease lapse, another worker
# would claim it, and the run would be executed twice — paid for twice — while
# every log line looked ordinary.


def test_a_tick_does_not_outlast_a_lease() -> None:
    """The budget is an order of magnitude below the shortest sensible lease.

    Asserted as a relationship between the two defaults rather than by running a
    worker, because that is what actually has to hold: a tick bounded at five
    seconds cannot threaten a ninety-second lease no matter how slow its items
    are, and a future change to either number is what this catches.
    """
    from docdoc.runs.identity import DEFAULT_LEASE

    worst_case_ms = maintenance.DEFAULT_BUDGET_MS + (
        identity.DEFAULT_DELIVERY_TIMEOUT_SECONDS * 1000
    )
    lease_ms = DEFAULT_LEASE.total_seconds() * 1000

    assert worst_case_ms < lease_ms, (
        f"one tick can take up to {worst_case_ms}ms and a lease is {lease_ms}ms. "
        f"A tick that outlasts a lease lets a live run be claimed by a second "
        f"worker and executed twice (SC-024)"
    )
    # And with room to spare: a heartbeat fires at a third of the lease, so the
    # tick must not eat the interval between two of them either.
    assert worst_case_ms < lease_ms / 2


def test_a_lease_survives_a_tick() -> None:
    """The behavioural half: a run claimed before a tick is still that worker's
    after it, and the worker can still heartbeat it.

    `heartbeat` returning `False` is defined to mean "you have been superseded
    and must abandon what you are doing". If a tick could cause that, a worker
    would discard completed work for no reason a log would explain.
    """
    from datetime import timedelta
    from uuid import uuid4

    from docdoc.runs.identity import DEFAULT_LEASE
    from tests.fixtures.run_queue import InMemoryRunQueue

    queue = InMemoryRunQueue()

    class _Spec:
        tenant_id = "acme"
        blob_id = "sha256:" + "a" * 64
        schema_identity = "invoice@1"
        request_id = None
        idempotency_key = None

    run = queue.submit(_Spec(), run_id=uuid4(), now=AT, expires_at=AT + timedelta(days=30))
    claimed = queue.claim(worker_id="w1", now=AT, lease=DEFAULT_LEASE, max_attempts=3)
    assert claimed is not None
    assert claimed.run_id == run.run_id

    # A tick, at the instant the worker holds the lease.
    maintenance.tick(
        maintenance.MaintenanceDeps(
            queue=queue,
            stores_for=lambda _: retention.Stores(artifacts=_Nothing(), blobs=_Nothing()),
        ),
        now=AT,
        retention_period=timedelta(days=1),
        batch=10,
    )

    # The worst case the tick may consume, applied to the clock.
    after = AT + timedelta(
        milliseconds=maintenance.DEFAULT_BUDGET_MS
        + identity.DEFAULT_DELIVERY_TIMEOUT_SECONDS * 1000
    )

    assert queue.heartbeat(run.run_id, now=after, lease=DEFAULT_LEASE, worker_id="w1"), (
        "the worker lost its lease across a maintenance tick, so the run it is "
        "still executing can be claimed and executed a second time (SC-024)"
    )


def test_a_sweep_never_considers_the_leased_run() -> None:
    """And the tick must not have swept it either.

    A run under an unexpired lease is never a retention candidate (FR-003) —
    which is the other way a tick could lose one, and the more alarming: not a
    lease lapsing, but the row going while a worker still holds it.
    """
    from datetime import timedelta
    from uuid import uuid4

    from docdoc.runs.identity import DEFAULT_LEASE
    from tests.fixtures.run_queue import InMemoryRunQueue

    queue = InMemoryRunQueue()

    class _Spec:
        tenant_id = "acme"
        blob_id = "sha256:" + "a" * 64
        schema_identity = "invoice@1"
        request_id = None
        idempotency_key = None

    # Expired the moment it was created, so only the lease protects it.
    run = queue.submit(_Spec(), run_id=uuid4(), now=AT, expires_at=AT - timedelta(days=1))
    queue.claim(worker_id="w1", now=AT, lease=DEFAULT_LEASE, max_attempts=3)

    report = maintenance.tick(
        maintenance.MaintenanceDeps(
            queue=queue,
            stores_for=lambda _: retention.Stores(artifacts=_Nothing(), blobs=_Nothing()),
        ),
        now=AT,
        retention_period=timedelta(days=1),
        batch=10,
    )

    assert report.swept.runs == 0
    assert queue.get(run.run_id, "acme") is not None
