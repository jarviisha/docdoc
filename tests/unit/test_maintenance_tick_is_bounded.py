"""One tick of unrequested work: deliveries first, then the sweep, under a budget.

**The ordering and the budget were untested.** `test_sweep_bounded_and_degraded.py`
covers `retention.sweep`; nothing covered the tick that calls it — so "deliveries
before the sweep", "never raises into the claim loop", and "the budget is checked
between attempts and never within one" were three statements in a docstring and
nowhere else.

They are the three that matter for a worker. A tick that raised would stop a
process from claiming runs, which is the thing a deployment is paid for; a tick
that cut a delivery mid-flight would produce an attempt nobody can classify.

`monotonic_ms` is a parameter, which is what makes any of this checkable — the
budget comes from a source the caller supplies rather than an ambient timer, for
the same reason `now` does everywhere else in this package (FR-096a).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from docdoc.runs import maintenance, retention
from docdoc.runs.errors import RunStateUnavailableError

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


@dataclass
class _Delivery:
    """Only the field the tick reads back off an attempt."""

    state: str = "delivered"


class _Deliverer:
    """Counts what it was asked to do, and can be told to fail."""

    def __init__(self, due: int = 0, *, state: str = "delivered", raises: Exception | None = None):
        self._due = tuple(_Delivery() for _ in range(due))
        self._state = state
        self._raises = raises
        self.attempts = 0

    def due(self, *, at: datetime, limit: int):
        if isinstance(self._raises, Exception) and self.attempts == 0 and self._due == ():
            raise self._raises
        return self._due[:limit]

    def attempt(self, delivery: Any, *, at: datetime):
        self.attempts += 1
        if self._raises is not None:
            raise self._raises
        return _Delivery(self._state)


class _Queue:
    """Enough of a queue for a sweep that finds nothing."""

    def expiring(self, *, now: datetime, limit: int, tenant_id: str | None = None):
        return ()

    def retained_ids_for(self, *args: Any, **kwargs: Any):
        return frozenset()


def _deps(**kwargs: Any) -> maintenance.MaintenanceDeps:
    return maintenance.MaintenanceDeps(
        queue=kwargs.pop("queue", _Queue()),  # type: ignore[arg-type]
        stores_for=kwargs.pop("stores_for", lambda _: retention.Stores(artifacts=None, blobs=None)),
        **kwargs,
    )


def _clock(*readings: float):
    """A monotonic source that returns each reading in turn, then the last.

    A list rather than a real timer: a budget test driven by wall-clock time is a
    flaky test waiting for a loaded CI runner.
    """
    values = list(readings)

    def source() -> float:
        return values.pop(0) if len(values) > 1 else values[0]

    return source


# -- nothing configured ------------------------------------------------------


def test_a_tick_with_nothing_configured_does_nothing(caplog: pytest.LogCaptureFixture) -> None:
    """Both defaults. A Milestone 9 worker upgrades and behaves identically."""
    report = maintenance.tick(_deps(), now=NOW, retention_period=None, batch=10)

    assert report.attempted == 0
    assert report.delivered == 0
    assert report.swept.runs == 0
    assert report.exhausted is False


# -- deliveries --------------------------------------------------------------


def test_every_due_delivery_is_attempted_and_the_accepted_ones_counted() -> None:
    """Two numbers rather than one: "tried 32, 0 landed" and "tried 0" are
    different facts about a deployment."""
    deliverer = _Deliverer(due=3)

    report = maintenance.tick(_deps(deliverer=deliverer), now=NOW, retention_period=None, batch=10)

    assert (report.attempted, report.delivered) == (3, 3)


def test_a_receiver_that_refuses_is_attempted_and_not_counted_as_delivered() -> None:
    report = maintenance.tick(
        _deps(deliverer=_Deliverer(due=2, state="failed")),
        now=NOW,
        retention_period=None,
        batch=10,
    )

    assert (report.attempted, report.delivered) == (2, 0)


def test_the_batch_bounds_what_one_tick_attempts() -> None:
    deliverer = _Deliverer(due=10)

    report = maintenance.tick(
        _deps(deliverer=deliverer), now=NOW, retention_period=None, batch=10, delivery_batch=4
    )

    assert report.attempted == 4


def test_the_budget_is_checked_between_attempts_and_never_within_one() -> None:
    """FR-062. Cutting a delivery mid-flight produces an attempt nobody can
    classify — neither delivered nor failed, with a receiver that may or may not
    have processed it.

    The clock reads under budget once and over it afterwards, so exactly one
    attempt starts and it is allowed to finish.
    """
    deliverer = _Deliverer(due=5)

    report = maintenance.tick(
        _deps(deliverer=deliverer),
        now=NOW,
        retention_period=None,
        batch=10,
        budget_ms=100,
        monotonic_ms=_clock(0, 0, 500),
    )

    assert deliverer.attempts == 1
    assert report.exhausted is True


def test_a_store_that_goes_away_stops_the_step_rather_than_the_worker(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Never raises into the claim loop.

    `attempt` is defined not to raise for a delivery *outcome*, so an exception
    here is the store having gone away underneath it. The runs this worker exists
    to execute are unaffected and the next tick tries again.
    """
    deliverer = _Deliverer(due=3, raises=RunStateUnavailableError("gone"))

    with caplog.at_level("WARNING", logger="docdoc.runs"):
        report = maintenance.tick(
            _deps(deliverer=deliverer), now=NOW, retention_period=None, batch=10
        )

    assert deliverer.attempts == 1, "it stopped rather than working through the batch"
    assert report.delivered == 0
    assert any(
        getattr(record, "docdoc", {}).get("event") == "maintenance.failed"
        for record in caplog.records
    ), "and it said so"


def test_a_queue_that_cannot_be_asked_what_is_due_reports_nothing(
    caplog: pytest.LogCaptureFixture,
) -> None:
    deliverer = _Deliverer(due=0, raises=RunStateUnavailableError("gone"))

    with caplog.at_level("WARNING", logger="docdoc.runs"):
        report = maintenance.tick(
            _deps(deliverer=deliverer), now=NOW, retention_period=None, batch=10
        )

    assert report.attempted == 0


# -- the sweep ---------------------------------------------------------------


def test_a_sweep_that_finds_nothing_stops_rather_than_spinning() -> None:
    """The loop's exit condition. Without it a tick with a live budget and an
    empty queue would sweep until the budget ran out, every minute, for ever."""
    report = maintenance.tick(_deps(), now=NOW, retention_period=timedelta(days=30), batch=10)

    assert report.swept.runs == 0
    assert report.exhausted is False


def test_a_sweep_that_fails_leaves_the_deliveries_it_already_did(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The report is not discarded when the second step fails.

    A tick that returned an empty report because the sweep raised would tell an
    operator no deliveries went out when three did.
    """

    class _Broken(_Queue):
        def expiring(self, *, now: datetime, limit: int, tenant_id: str | None = None):
            raise RunStateUnavailableError("gone")

    with caplog.at_level("WARNING", logger="docdoc.runs"):
        report = maintenance.tick(
            _deps(queue=_Broken(), deliverer=_Deliverer(due=3)),
            now=NOW,
            retention_period=timedelta(days=30),
            batch=10,
        )

    assert report.attempted == 3
    assert report.swept.runs == 0


# -- the counter table -------------------------------------------------------


def test_spent_counter_windows_are_expired() -> None:
    """T175b. A counter table that only grows is the failure this feature exists
    to prevent, arriving by the back door."""
    expired: list[dict] = []

    class _Limiter:
        def expire_windows(self, *, now: datetime, keep: timedelta) -> int:
            expired.append({"now": now, "keep": keep})
            return 1

    maintenance.tick(_deps(limiter=_Limiter()), now=NOW, retention_period=None, batch=10)

    assert len(expired) == 1
    assert expired[0]["now"] == NOW


def test_a_limiter_that_cannot_expire_is_silent_and_not_fatal(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Best-effort. A counter row that outlives its window refuses nothing and
    permits nothing — it is only ever storage."""

    class _Limiter:
        def expire_windows(self, *, now: datetime, keep: timedelta) -> int:
            raise RunStateUnavailableError("gone")

    report = maintenance.tick(_deps(limiter=_Limiter()), now=NOW, retention_period=None, batch=10)

    assert report.attempted == 0


def test_counters_are_not_expired_once_the_budget_is_spent() -> None:
    """It shares the sweep's deadline, and it is the third step for that reason."""
    expired: list[int] = []

    class _Limiter:
        def expire_windows(self, *, now: datetime, keep: timedelta) -> int:
            expired.append(1)
            return 0

    maintenance.tick(
        _deps(limiter=_Limiter()),
        now=NOW,
        retention_period=None,
        batch=10,
        budget_ms=100,
        monotonic_ms=_clock(0, 500),
    )

    assert expired == []


# -- the worker's side of it -------------------------------------------------
#
# `Worker._maintain` decides *whether* to tick and swallows whatever the tick
# raised. Both were untested, and both are about the same promise: a maintenance
# failure must not stop a worker claiming runs, because the runs are what a
# deployment is paid for.


def _worker(**kwargs: Any):
    """A worker with nothing but the fields a tick reads.

    `blobs`, `store`, `registry` and `adapter` are `None` because `_maintain`
    touches none of them — it is called between claims, never with a run in
    flight, which is the property that keeps a tick from delaying a heartbeat
    into losing a lease.
    """
    from docdoc.runs.worker import Worker

    return Worker(
        queue=kwargs.pop("queue", _Queue()),  # type: ignore[arg-type]
        blobs=None,  # type: ignore[arg-type]
        store=None,  # type: ignore[arg-type]
        registry=None,
        adapter=None,
        worker_id="test:1",
        stores_for=lambda _: (None, None),
        **kwargs,
    )


def test_a_worker_with_nothing_configured_does_not_tick() -> None:
    """No retention, no deliverer, no limiter: a Milestone 9 worker.

    Checked before the interval rather than after, so an upgraded deployment
    that configured none of the three pays for no clock read either.
    """
    worker = _worker()

    worker._maintain()

    assert worker._ticker._last is None


def test_a_configured_worker_ticks_and_records_that_it_did() -> None:
    worker = _worker(retention_period=timedelta(days=30))

    worker._maintain()

    assert worker._ticker._last is not None


def test_a_tick_that_raises_does_not_stop_the_claim_loop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The promise this method exists for.

    `maintenance.tick` is defined not to raise, so an exception here is
    something further down having gone wrong in a way nobody anticipated —
    which is exactly when swallowing it matters.
    """

    class _Exploding(_Queue):
        def expiring(self, *, now: datetime, limit: int, tenant_id: str | None = None):
            raise TypeError("something nobody anticipated")

    worker = _worker(queue=_Exploding(), retention_period=timedelta(days=30))

    with caplog.at_level("WARNING", logger="docdoc.runs"):
        worker._maintain()

    assert any("maintenance.failed" in record.getMessage() for record in caplog.records)
    # And the tick is still recorded, so a failing tick cannot spin: `finally`.
    assert worker._ticker._last is not None


def test_a_second_call_inside_the_interval_does_nothing() -> None:
    """FR-114. A busy worker runs this at most once per interval."""
    worker = _worker(retention_period=timedelta(days=30), maintenance_interval_seconds=3600)

    worker._maintain()
    first = worker._ticker._last
    worker._maintain()

    assert worker._ticker._last == first
