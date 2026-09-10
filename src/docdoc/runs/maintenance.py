"""The work nobody requests, done between claims, bounded.

**No fifth process** (FR-116). Milestone 9's argument against a reaper is the
argument against a cron entry too: *"there is no reaper process to deploy, monitor,
and have fail silently"*, and an external scheduler is that process wearing the
operator's crontab. So the worker does this between claims, and a deployment that
runs a worker gets retention without arranging anything.

**No thread, no subprocess, no event loop.** Milestone 9's FR-025 forbids them in
the worker and its reason reaches here unchanged: a GIL-holding stage must not be
able to delay a sibling's heartbeat into losing a lease it still holds. FR-062 is
satisfied by **bounding the tick**, not by concurrency.

**Bounded between items and never within one.** A delivery has its own timeout and
cutting it mid-flight produces an attempt nobody can classify, so the worst case a
claim waits is one budget plus one item — and that is the number the operator
documentation states rather than a number nobody measured.

**Steps are added to a loop that already runs**, which is what keeps FR-116's
"no fifth process type" true by construction. The sweep landed in Phase 3,
because User Story 1 promises runs age out "on their own, on a schedule" and a
tick that arrived in the Polish phase would make that promise false for the
length of the milestone. Phase 7 added due deliveries **before** the sweep, and
the order is deliberate: a delivery is a promise to somebody outside the
deployment, a sweep is housekeeping, and when the budget runs out the
housekeeping is what should wait.

Every step is off unless configured. A deployment with no retention period and
no deliverer ticks in microseconds and touches nothing.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

from docdoc.runs import retention
from docdoc.runs.errors import RunError

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime, timedelta

    from docdoc.runs.queue import RunQueue

__all__ = [
    "DEFAULT_INTERVAL_SECONDS",
    "MaintenanceDeps",
    "TickReport",
    "Ticker",
    "tick",
]

_logger = logging.getLogger("docdoc.runs")

#: How often a tick may run. 60 seconds, so a worker claiming continuously does
#: not sweep on every iteration and an idle one still makes progress within a
#: minute (research R15).
DEFAULT_INTERVAL_SECONDS = 60

#: How long one tick may take in total, checked **between** items. Five seconds
#: is an order of magnitude below the shortest sensible lease, so a tick cannot
#: threaten a heartbeat even when every item it touches is slow.
DEFAULT_BUDGET_MS = 5000


@dataclass(frozen=True)
class MaintenanceDeps:
    """What a tick needs, passed in rather than reached for.

    ``stores_for`` is a callable because a batch may span tenants and a store
    instance is bound to one (ADR-0014 §3). ``monotonic`` is a parameter for the
    same reason ``now`` is everywhere else in this package: `identity.py` is the
    only module permitted to read a clock (FR-096a), and a tick whose budget came
    from an ambient timer could not be tested at all.
    """

    queue: RunQueue
    stores_for: Callable[[str], retention.Stores]
    corrections: retention.CorrectionStore | None = None
    #: ``None`` means this deployment delivers nothing, which is the default and
    #: is what FR-064 requires of one that registered no callbacks: no outbound
    #: request, and no dependency to have one.
    deliverer: Any = None
    #: Only for expiring spent counter windows. A tick enforces nothing — limits
    #: are checked at submission and nowhere else (FR-045) — and this is here so
    #: that a table which only ever grows does not become the accumulation
    #: problem the limits were configured to prevent.
    limiter: Any = None


@dataclass(frozen=True)
class TickReport:
    """What one tick did. Counts, and no identifiers (FR-012)."""

    swept: retention.SweepReport = field(default_factory=retention.SweepReport)
    #: Deliveries attempted, and of those, how many the receiver accepted. Two
    #: numbers rather than one, because "we tried 32 and 0 landed" and "we tried
    #: 32 and 32 landed" are the two states an operator needs to tell apart.
    attempted: int = 0
    delivered: int = 0
    #: True when the budget stopped the tick before it ran out of work. Not a
    #: failure: the next tick resumes, which is what `sweep`'s idempotence buys.
    exhausted: bool = False


def tick(
    deps: MaintenanceDeps,
    *,
    now: datetime,
    retention_period: timedelta | None,
    batch: int,
    delivery_batch: int = 32,
    budget_ms: int = DEFAULT_BUDGET_MS,
    monotonic_ms: Callable[[], float] | None = None,
) -> TickReport:
    """One bounded pass of unrequested work: deliveries, then the sweep.

    Returns having done nothing when neither is configured — ``retention_period``
    of ``None`` means retention is off (FR-014) and a ``deliverer`` of ``None``
    means nothing is delivered (FR-064). Both are the default.

    Never raises into the worker's claim loop. A maintenance failure is a
    degradation to log, not a reason to stop claiming runs — the runs are the
    thing a deployment is paid for.
    """
    elapsed = _elapsed(monotonic_ms)

    # **Deliveries first.** A delivery is a promise made to somebody outside the
    # deployment; a sweep is housekeeping that is idempotent and can wait for the
    # next tick without anybody noticing. When the budget runs out, the sweep is
    # what should be the thing that did not happen.
    report = _deliver(deps, now=now, limit=delivery_batch, elapsed=elapsed, budget_ms=budget_ms)

    # **Third step, and last** (T175b). A counter table that only grows is the
    # failure this whole feature exists to prevent, arriving by the back door: a
    # deployment that configured limits to stop unbounded accumulation would find
    # `limit_counters` accumulating instead.
    #
    # After the sweep's guard rather than before it, because it shares the sweep's
    # deadline: a window is expired when it is older than the longest limit
    # window, which is what `expire_counters` knows and this does not.
    if elapsed() < budget_ms:
        _expire_counters(deps, now=now)

    if retention_period is None:
        return report

    while elapsed() < budget_ms:
        try:
            swept = retention.sweep(
                deps.queue,
                deps.stores_for,
                now=now,
                batch=batch,
                corrections=deps.corrections,
            )
        except RunError as error:
            # The database went away, or the sweep refused. Either way the runs
            # this worker exists to execute are unaffected, and the next tick
            # tries again.
            _logger.warning(
                "maintenance tick failed",
                extra={
                    "docdoc": {
                        "event": "maintenance.failed",
                        "error": type(error).__name__,
                    }
                },
            )
            return report

        report = replace(report, swept=report.swept.merged(swept))
        if not swept.runs or swept.degraded:
            return report

    return replace(report, exhausted=True)


def _deliver(
    deps: MaintenanceDeps,
    *,
    now: datetime,
    limit: int,
    elapsed: Callable[[], float],
    budget_ms: int,
) -> TickReport:
    """Attempt the deliveries that are due, one at a time, checking the budget.

    **Between attempts and never within one** (FR-062). A delivery has its own
    timeout and cutting it mid-flight would produce an attempt nobody can
    classify — neither delivered nor failed, with a receiver that may or may not
    have processed it. So the worst case a claim waits is one budget plus one
    delivery timeout, and that is the number the operator documentation states
    rather than a number nobody measured.

    One failing receiver delays the deliveries behind it by its own timeout and
    nothing more, because `attempt` never raises: every outcome is a row.
    """
    if deps.deliverer is None:
        return TickReport()

    try:
        due = deps.deliverer.due(at=now, limit=limit)
    except RunError as error:
        _log_failure(error)
        return TickReport()

    attempted = delivered = 0
    for delivery in due:
        if elapsed() >= budget_ms:
            return TickReport(attempted=attempted, delivered=delivered, exhausted=True)
        attempted += 1
        try:
            result = deps.deliverer.attempt(delivery, at=now)
        except RunError as error:
            # `attempt` is defined not to raise for a delivery outcome, so this
            # is the store having gone away underneath it. Stop the step; the
            # sweep below will find the same thing and stop too.
            _log_failure(error)
            break
        if str(getattr(result, "state", "")) == "delivered":
            delivered += 1

    return TickReport(attempted=attempted, delivered=delivered)


def _expire_counters(deps: MaintenanceDeps, *, now: datetime) -> None:
    """Remove limit-counter windows that can never be read again.

    Best-effort and silent on failure. A counter row that outlives its window
    refuses nothing and permits nothing — it is only ever storage — so a tick
    that could not remove one has nothing to report and nothing to retry
    urgently. The next tick tries again.

    ``None`` for the limiter, which is the default, means there are no counters
    to expire because nothing counted.
    """
    from docdoc.runs.identity import LIMIT_RETENTION

    expire = getattr(deps.limiter, "expire_windows", None)
    if expire is None:
        return
    try:
        expire(now=now, keep=LIMIT_RETENTION)
    except RunError as error:
        _log_failure(error)


def _log_failure(error: Exception) -> None:
    _logger.warning(
        "maintenance tick failed",
        extra={"docdoc": {"event": "maintenance.failed", "error": type(error).__name__}},
    )


class Ticker:
    """Decides *whether* to tick, so the worker loop does not have to.

    Holds the last tick's instant and nothing else. Separate from `tick` because
    "is it time" and "do the work" are different questions and only the second is
    worth testing against a store.
    """

    __slots__ = ("_interval_seconds", "_last")

    def __init__(self, *, interval_seconds: int = DEFAULT_INTERVAL_SECONDS) -> None:
        self._interval_seconds = interval_seconds
        self._last: datetime | None = None

    def due(self, now: datetime) -> bool:
        """Whether enough time has passed. The first call is always due."""
        if self._last is None:
            return True
        return (now - self._last).total_seconds() >= self._interval_seconds

    def ran(self, now: datetime) -> None:
        self._last = now


def _elapsed(monotonic_ms: Callable[[], float] | None) -> Callable[[], float]:
    """Milliseconds since this call, from a source the caller may supply.

    Defaults to `time.monotonic`, reached through `identity` rather than
    imported here — `tests/unit/test_runs_clock_confinement.py` permits exactly
    one module in this package to touch an ambient source, and widening it is
    what FR-096a forbids.
    """
    from docdoc.runs.identity import monotonic_ms as default_source

    source: Any = monotonic_ms or default_source
    start = source()
    return lambda: source() - start
