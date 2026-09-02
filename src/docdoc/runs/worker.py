"""The worker: claim, run, record. One run at a time.

The loop is deliberately small, because everything hard was already solved
somewhere else. `pipeline.run()` is a function from inputs to a result; the
artifact store already checkpoints each stage; the claim query already handles
lease expiry. What is left here is transport.

**One run per process** (FR-025, research R9a). No `--concurrency`, no threads
beyond the heartbeat, no subprocess pool. PyMuPDF and `rapidfuzz` hold the GIL in
bursts, so a threaded worker lets one long parse starve a sibling's heartbeat
until that sibling loses a lease it is still executing — a utilisation choice
becoming a correctness bug. Concurrency is replica count.

**A crash anywhere is safe.** If the process dies between `pipeline.run()`
returning and `finish()` committing, the lease lapses, the run is redelivered,
and every completed stage's artifact is reused — so the redelivered attempt
recomputes the same `processing_id` and repeats at most the stage that was in
flight (ADR-0013 §4). That is the artifact chain paying for something it was not
designed for: per-stage checkpointing was built for prompt-change reuse and turns
out to be crash recovery, because both ask *what of this work is already known?*

**Nothing here reads a clock.** `now` is passed in from `identity.py` (FR-072).
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from typing import TYPE_CHECKING, Any

from docdoc.runs import identity
from docdoc.runs.errors import RunStateUnavailableError
from docdoc.runs.identity import DEFAULT_LEASE
from docdoc.runs.model import RunOutcome, RunStatus
from docdoc.runs.observe import log_transition

if TYPE_CHECKING:
    from datetime import datetime, timedelta

    from docdoc.artifacts import ArtifactStore, BlobStore
    from docdoc.pipeline.result import PipelineResult
    from docdoc.runs.model import Run
    from docdoc.runs.queue import RunQueue

__all__ = ["DEFAULT_MAX_ATTEMPTS", "Worker", "execute_one"]

#: Three, because the failure this bounds is the poison document, and a document
#: that terminates three workers will terminate thirty (research R9).
DEFAULT_MAX_ATTEMPTS = 3

#: A third of the lease, so a live worker misses two ticks before losing a run it
#: still holds.
_HEARTBEAT_FRACTION = 3

#: How long an idle worker waits before asking again. Short enough that
#: claim-to-start stays under a second; long enough that an idle pool is not a
#: load generator.
_IDLE_SLEEP_SECONDS = 0.5

_logger = logging.getLogger("docdoc.runs")


def execute_one(
    run: Run,
    *,
    queue: RunQueue,
    blobs: BlobStore,
    store: ArtifactStore,
    registry: Any,
    adapter: Any,
    now: datetime,
    limits: Any = None,
) -> PipelineResult | None:
    """Execute one claimed run and record what happened.

    Returns the `PipelineResult` when the pipeline ran, and `None` when it never
    got that far — a withdrawn schema or a missing blob, both of which are
    terminal without reaching a stage.

    Extracted from the loop so that a test can drive exactly one run without a
    thread, a signal handler, or a sleep. `tests/contract/test_async_matches_sync.py`
    is the reason: SC-001 is a question about *a* run, and answering it should not
    require starting a worker and waiting for it to notice.
    """
    from docdoc.pipeline import run as run_pipeline

    # Resolve the schema before anything else. A withdrawn schema is a
    # configuration fault, not a transient one, so it fails terminally on the
    # first occurrence rather than being retried into `RunAbandonedError` — a
    # word that would send an operator to look at the document (FR-091).
    try:
        registry.resolve(run.schema_identity)
    except Exception as exc:
        _finish(
            queue,
            run,
            RunOutcome(
                status=RunStatus.FAILED,
                failed_stage=None,
                error_class=type(exc).__name__,
            ),
            now=now,
        )
        return None

    data = blobs.get(run.blob_id)
    if data is None:
        _finish(
            queue,
            run,
            RunOutcome(status=RunStatus.FAILED, failed_stage=None, error_class="UnknownBlob"),
            now=now,
        )
        return None

    result = run_pipeline(
        data,
        schema=run.schema_identity,
        registry=registry,
        adapter=adapter,
        store=store,
        limits=limits,
        request_id=run.request_id,
    )

    _finish(queue, run, RunOutcome.of(result), now=now)
    return result


def _finish(queue: RunQueue, run: Run, outcome: RunOutcome, *, now: datetime) -> None:
    queue.finish(run.run_id, outcome, now=now)
    log_transition(
        run_id=run.run_id,
        tenant_id=run.tenant_id,
        from_state=str(run.status),
        to_state=str(outcome.status),
        attempts=run.attempts,
        worker_id=run.worker_id,
        reason=outcome.error_class or "completed",
    )


class Worker:
    """Claim, execute, record — until asked to stop.

    Holds no state a restart cannot recover from the run table. `worker_id` is a
    diagnostic string and nothing routes on it; worker liveness *is* the lease,
    which is why there is no registry of workers to disagree with reality.
    """

    def __init__(
        self,
        *,
        queue: RunQueue,
        blobs: BlobStore,
        store: ArtifactStore,
        registry: Any,
        adapter: Any,
        worker_id: str,
        lease: timedelta = DEFAULT_LEASE,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        limits: Any = None,
    ) -> None:
        self._queue = queue
        self._blobs = blobs
        self._store = store
        self._registry = registry
        self._adapter = adapter
        self._worker_id = worker_id
        self._lease = lease
        self._max_attempts = max_attempts
        self._limits = limits
        self._stopping = threading.Event()

    def stop(self) -> None:
        """Ask the loop to finish its current run and exit."""
        self._stopping.set()

    def install_signal_handlers(self) -> None:
        """Stop claiming on SIGTERM and SIGINT.

        The handler only sets a flag. Doing the work here would run it on
        whatever stack the signal interrupted, which for a run mid-pipeline is
        every stack in the process.
        """
        for received in (signal.SIGTERM, signal.SIGINT):
            signal.signal(received, lambda *_: self.stop())

    def run_forever(self) -> None:
        """The loop. Returns when `stop()` has been called and work is done."""
        while not self._stopping.is_set():
            try:
                claimed = self._queue.claim(
                    worker_id=self._worker_id,
                    now=identity.now(),
                    lease=self._lease,
                    max_attempts=self._max_attempts,
                )
            except RunStateUnavailableError:
                # The database is unreachable. Not fatal: it is exactly the state
                # readiness reports and an operator is already fixing, and a
                # worker that exited would need restarting afterwards.
                _logger.warning('{"event": "runs.queue_unavailable"}')
                time.sleep(_IDLE_SLEEP_SECONDS)
                continue

            if claimed is None:
                self._stopping.wait(_IDLE_SLEEP_SECONDS)
                continue

            self._execute_with_heartbeat(claimed)

    def _execute_with_heartbeat(self, run: Run) -> None:
        heartbeat = _Heartbeat(self._queue, run, self._lease)
        heartbeat.start()
        try:
            execute_one(
                run,
                queue=self._queue,
                blobs=self._blobs,
                store=self._store,
                registry=self._registry,
                adapter=self._adapter,
                now=identity.now(),
                limits=self._limits,
            )
        finally:
            heartbeat.stop()

        if self._stopping.is_set():
            # Shutting down between runs. `release` is unnecessary here — the run
            # just finished — and is what the loop would call if it were stopping
            # with one still claimed (FR-043).
            return


class _Heartbeat:
    """Extends one run's lease on a timer, and stops when the run does.

    The only thread a worker has (FR-025). It exists because a lease sized to the
    slowest document would make every crash cost that long in redelivery latency,
    and a lease sized to the *heartbeat* costs ninety seconds regardless of how
    long the document takes.
    """

    def __init__(self, queue: RunQueue, run: Run, lease: timedelta) -> None:
        self._queue = queue
        self._run = run
        self._lease = lease
        self._interval = lease.total_seconds() / _HEARTBEAT_FRACTION
        self._stopping = threading.Event()
        self._thread = threading.Thread(target=self._beat, daemon=True)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stopping.set()
        self._thread.join(timeout=self._interval)

    def _beat(self) -> None:
        while not self._stopping.wait(self._interval):
            try:
                held = self._queue.heartbeat(
                    self._run.run_id, now=identity.now(), lease=self._lease
                )
            except RunStateUnavailableError:
                continue

            if not held:
                # Superseded: the lease lapsed and another worker claimed the run.
                # Recorded and not acted on — this thread cannot safely interrupt
                # a provider call, and the `finish` that follows is a no-op
                # because the row is no longer claimable by this attempt.
                _logger.warning('{"event": "runs.lease_lost", "run_id": "%s"}', self._run.run_id)
                return
