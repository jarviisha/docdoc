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

from docdoc.artifacts.errors import ArtifactError
from docdoc.runs import identity
from docdoc.runs.errors import RunStateUnavailableError
from docdoc.runs.identity import DEFAULT_LEASE, DEFAULT_MAX_ATTEMPTS
from docdoc.runs.model import RunOutcome, RunStatus

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime, timedelta

    from docdoc.artifacts import ArtifactStore, BlobStore
    from docdoc.pipeline.result import PipelineResult
    from docdoc.runs.model import Run
    from docdoc.runs.queue import RunQueue

#: ``DEFAULT_MAX_ATTEMPTS`` is re-exported rather than defined here. It moved to
#: `identity.py` when `DOCDOC_RUN_MAX_ATTEMPTS` arrived, so that a default and
#: the environment override of it sit in one module instead of two. The name
#: stays importable from here because that is where callers already look for it.
__all__ = ["DEFAULT_MAX_ATTEMPTS", "Worker", "execute_one"]

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
    stopping: Callable[[], bool] | None = None,
) -> PipelineResult | None:
    """Execute one claimed run and record what happened.

    Returns the `PipelineResult` when the pipeline ran, and `None` when it never
    got that far — a withdrawn schema or a missing blob, both of which are
    terminal without reaching a stage.

    `stopping` reports that **this process** is shutting down, and it is what
    makes FR-042's "finish or relinquish" have a second branch. Without it the
    only thing that stops a run between stages is a caller's cancellation, so a
    signalled worker had to run the current document to completion — minutes,
    against an orchestrator's grace period of seconds — and then be killed
    mid-run anyway, leaving the run to wait out its whole lease.

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

    # A blob that is *absent* is terminal — nothing will make it appear. A blob
    # store that cannot be *reached* is not, and conflating the two is how a
    # five-second outage used to fail every run claimed during it, permanently,
    # with an error class that sends the operator to look for a document that is
    # sitting right there. `StoreUnavailable` propagates instead, so the lease
    # lapses and the run is redelivered — which is what the lease is for.
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
        # Consulted at stage boundaries only (R4, contracts/runs-layer.md). One
        # query per boundary — three per run — against a row this worker already
        # holds a lease on, which is cheap next to the stage it decides whether
        # to start.
        #
        # A query rather than a flag passed in at claim time: cancellation
        # arrives *while* the run is executing, so anything read once at the
        # start would answer the question that was not asked.
        #
        # An unreachable database means "keep going". The alternative — treating
        # a failed read as a cancellation — would stop every running run in the
        # fleet during a database blip, which is a far more expensive way to be
        # wrong than finishing a run somebody asked to stop.
        #
        # Shutdown is the second reason to stop, and it is checked first because
        # it costs nothing: it is a flag in this process, where the other is a
        # round trip.
        should_continue=lambda: (
            not (stopping and stopping()) and not _cancellation_requested(queue, run)
        ),
    )

    outcome = RunOutcome.of(result)

    # **Stopped between stages with nobody having asked.** The only other thing
    # that stops a run at a boundary is this process shutting down, so the run is
    # not cancelled — it is unfinished, and it belongs back in the queue rather
    # than in a terminal state naming a cancellation nobody requested.
    #
    # `release` rather than letting the lease lapse is FR-043: a rolling restart
    # should cost nothing, and waiting out a ninety-second lease per replica is
    # not nothing. Every completed stage kept its artifact, so the worker that
    # picks it up resumes rather than restarts.
    if outcome.status is RunStatus.CANCELLED and not _cancellation_requested(queue, run):
        queue.release(run.run_id, now=now, worker_id=run.worker_id)
        return result

    # **A `processing_id` is a promise that the identity resolves**, and until
    # this check it was one the worker could not keep. Both artifact stores
    # degrade rather than fail when a write is dropped (FR-063) — right for the
    # three intermediate stages, which are a cache, and wrong for the last one,
    # whose id becomes `processing_id` and is the only handle the caller is given
    # on the result. A denied `PutObject` on the validation stage alone produced
    # `succeeded` plus an id that `GET /v1/jobs/{id}/result` answers `unknown`
    # about, for ever, with nothing logged as an error anywhere.
    #
    # Checked here rather than in the pipeline because this is where the promise
    # is made: the synchronous route returns the result in its own response and
    # needs no store to have kept it.
    if outcome.status is RunStatus.SUCCEEDED and outcome.processing_id is not None:
        outcome = _demand_the_result_is_retrievable(store, outcome)

    # `worker_id` makes this conditional on still holding the lease. Without it a
    # worker that stalled past its lease could overwrite the verdict of the worker
    # that superseded it — see `RunQueue.finish`.
    queue.finish(run.run_id, outcome, now=now, worker_id=run.worker_id)
    return result


def _demand_the_result_is_retrievable(store: ArtifactStore, outcome: RunOutcome) -> RunOutcome:
    """The same outcome, or a failed one when the result did not survive.

    One metadata read against a store this process has just written to, on the
    success path only. That is cheap next to the four stages behind it, and it is
    the only thing standing between "the store dropped the write" and a caller
    holding an identity that resolves to nothing.

    A read that *fails* counts as not retrievable, and deliberately so: if the
    store cannot be reached now, the result cannot be fetched by the caller
    either, so reporting `succeeded` would be describing a result nobody can get.
    """
    try:
        stored = store.envelope(outcome.processing_id or "")
    except Exception:
        # Broad on purpose: every way this can fail — unreachable, corrupt,
        # refused — means the caller cannot retrieve the result either.
        stored = None
    if stored is not None:
        return outcome

    _logger.error(
        '{"event": "runs.result_not_stored", "processing_id": "%s"}',
        outcome.processing_id,
    )
    return RunOutcome(
        status=RunStatus.FAILED,
        failed_stage="validation",
        # Not a stage failure: validation ran and produced a result. What failed
        # is keeping it, and the class says so rather than blaming the document.
        error_class="ResultNotStored",
        stage_outcomes=outcome.stage_outcomes,
    )


def _cancellation_requested(queue: RunQueue, run: Run) -> bool:
    """Whether this run has been asked to stop, tolerating an outage."""
    try:
        return queue.is_cancelled(run.run_id)
    except RunStateUnavailableError:
        return False


def _finish(queue: RunQueue, run: Run, outcome: RunOutcome, *, now: datetime) -> None:
    """Record a terminal state.

    The event that used to be emitted here now comes from the queue, which is
    where the transition happens — see `docdoc.runs.observe.log_transition`. This
    wrapper survives because it is the one place `execute_one`'s two
    never-reached-a-stage paths converge.

    Passes `worker_id` for the same reason the success path does: only the worker
    that holds the run may conclude it.
    """
    queue.finish(run.run_id, outcome, now=now, worker_id=run.worker_id)


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
        health_port: int | None = None,
        stores_for: Callable[[str], tuple[Any, Any]] | None = None,
    ) -> None:
        self._queue = queue
        self._blobs = blobs
        self._store = store
        #: How to reach one tenant's namespace. Without it a worker executes
        #: every run against the stores it was constructed with, which are the
        #: **default** tenant's — see `_stores_for`.
        self._store_factory = stores_for
        self._by_tenant: dict[str, tuple[Any, Any]] = {}
        self._registry = registry
        self._adapter = adapter
        self._worker_id = worker_id
        self._lease = lease
        self._max_attempts = max_attempts
        self._limits = limits
        self._health_port = health_port
        self._stopping = threading.Event()
        self._health: _HealthServer | None = None

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
        self._serve_health()
        try:
            self._claim_forever()
        finally:
            if self._health is not None:
                self._health.stop()

    def _claim_forever(self) -> None:
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

            try:
                self._execute_with_heartbeat(claimed)
            except RunStateUnavailableError:
                # The database went away *after* the claim — while finishing or
                # releasing. Survivable for exactly the reason the claim's own
                # handler gives above, and it was not handled here: the run is
                # left unfinished, so its lease lapses and another attempt picks
                # it up with every completed stage's artifact intact.
                #
                # Untreated, this was the asymmetry that mattered most in an
                # outage: an unreachable database at `claim` logged a warning and
                # retried, and the identical error one moment later at `finish`
                # terminated the process. A database blip therefore killed
                # precisely the workers that were doing work.
                _logger.warning(
                    '{"event": "runs.queue_unavailable", "run_id": "%s"}', claimed.run_id
                )
                self._stopping.wait(_IDLE_SLEEP_SECONDS)
            except ArtifactError as unavailable:
                if unavailable.reason != "unavailable":
                    # A corrupt or conflicting artifact is a fault to surface,
                    # not to sleep through. Same distinction the pipeline makes.
                    raise
                # The store went away mid-run. The run is **not** finished, so
                # its lease lapses and another attempt picks it up with every
                # completed stage's artifact intact — the redelivery path, used
                # for the thing it was built for.
                #
                # Not `release`: that would requeue it instantly and this worker
                # would claim it again immediately, spinning against a store that
                # is still down. Waiting out the lease is the backoff.
                _logger.warning(
                    '{"event": "runs.store_unavailable", "run_id": "%s"}',
                    claimed.run_id,
                )
                self._stopping.wait(_IDLE_SLEEP_SECONDS)

    def _serve_health(self) -> None:
        """Answer the same two routes the API does, when asked to (FR-053).

        Off unless a port is given, and that is the honest default: a worker
        behind no load balancer has nobody to answer, and binding a port a
        deployment did not ask for is the kind of thing that collides on a host
        network. `docker compose` and the CLI both pass one.

        The server runs on its own thread. That makes two threads in a process
        whose docstring says the heartbeat is the only one — and the exception is
        narrow enough to state rather than to hide: this thread executes no run,
        touches no lease, and holds nothing the pipeline can see. The reason
        there is no *worker pool* is that PyMuPDF and `rapidfuzz` hold the GIL in
        bursts and would starve a heartbeat; a thread that answers two constant
        questions cannot starve anything.
        """
        if self._health_port is None:
            return
        from docdoc.runs.health import Readiness

        self._health = _HealthServer(
            Readiness(runs=self._queue, blobs=self._blobs), port=self._health_port
        )
        self._health.start()

    def _stores_for(self, tenant_id: str) -> tuple[Any, Any]:
        """The stores for one tenant's namespace (FR-084, FR-086).

        **The worker used to ignore `run.tenant_id` entirely** and execute every
        run against the pair it was constructed with. With authentication on that
        is wrong in both directions at once, and neither is loud. Tenant `acme`'s
        blob is written by the API at `<root>/t/acme/blobs/…`; the worker looked
        under `<root>/blobs/…`, found nothing, and finished the run `failed /
        UnknownBlob` — so no non-default tenant could complete an asynchronous run
        at all. Where a blob *was* found, the artifacts went to the unprefixed
        root, which is the shared namespace ADR-0014 exists to abolish: one
        tenant's results reusable by another at identities both derive from
        content.

        Cached per tenant, as `_Deployment.stores_for` is and for the same reason
        — a store is a client and a prefix, and rebuilding one per run would open
        a connection pool per run on the object-store path.

        Falls back to the constructed pair when no factory was given. That keeps
        every single-tenant caller and every test working unchanged, and it is
        honest: with one tenant, those *are* its stores.
        """
        if self._store_factory is None:
            return (self._store, self._blobs)
        cached = self._by_tenant.get(tenant_id)
        if cached is None:
            cached = self._store_factory(tenant_id)
            self._by_tenant[tenant_id] = cached
        return cached

    def _execute_with_heartbeat(self, run: Run) -> None:
        store, blobs = self._stores_for(run.tenant_id)
        heartbeat = _Heartbeat(self._queue, run, self._lease)
        heartbeat.start()
        try:
            execute_one(
                run,
                queue=self._queue,
                blobs=blobs,
                store=store,
                registry=self._registry,
                adapter=self._adapter,
                now=identity.now(),
                limits=self._limits,
                # FR-042's "finish or relinquish". `execute_one` stops the
                # pipeline at the next stage boundary once this reads True, and
                # releases the run so it re-queues immediately (FR-043).
                stopping=self._stopping.is_set,
            )
        finally:
            heartbeat.stop()


class _HealthServer:
    """`/healthz` and `/readyz` on a worker, from the standard library.

    **Not FastAPI**, and that is the requirement rather than a preference. A
    worker runs on a base install plus `docdoc[postgres]`; importing the HTTP
    framework here would make `docdoc[api]` a dependency of running a worker, and
    it would make `docdoc.runs` import `docdoc.api`, which the layer contract
    forbids outright.

    The bodies come from `docdoc.runs.health`, so the two process types answer
    byte-identically. An orchestrator configures one probe for both, which is the
    whole point of FR-053 saying "both process types".
    """

    def __init__(self, readiness: Any, *, port: int, host: str = "0.0.0.0") -> None:
        self._readiness = readiness
        self._port = port
        self._host = host
        self._server: Any = None
        self._thread: threading.Thread | None = None

    @property
    def bound_port(self) -> int:
        """The port actually listening, which is not always the one requested.

        Port `0` means "any free one", and without this there is no way to find
        out which — so a test either hard-codes a port and races whatever else on
        the machine wants it, or does not run. The whole of this server was
        untested for exactly that reason.

        Useful beyond the suite: an operator who passed `--health-port 0` has the
        same question, and a process that cannot say which port it bound is one
        nothing can probe.
        """
        if self._server is None:
            raise RuntimeError("the health server has not started")
        return int(self._server.server_address[1])

    def start(self) -> None:
        import json as _json
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        from docdoc.runs.health import (
            LIVENESS_PATH,
            READINESS_PATH,
            liveness_body,
            readiness_body,
        )

        readiness = self._readiness

        class _Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # the standard library's spelling, not ours
                if self.path == LIVENESS_PATH:
                    self._answer(200, liveness_body())
                    return
                if self.path == READINESS_PATH:
                    unmet = readiness.unmet()
                    self._answer(200 if not unmet else 503, readiness_body(unmet))
                    return
                # A worker serves no API. Anything else is 404 with no body
                # naming what does exist, because this port is reachable by
                # whoever can reach the worker and should describe nothing.
                self._answer(404, {"status": "not_found"})

            def _answer(self, status: int, body: dict[str, Any]) -> None:
                payload = _json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                # Silenced deliberately. `BaseHTTPRequestHandler` writes a line
                # to stderr per request, and a probe every five seconds per
                # replica would drown every run event this process emits.
                return

        self._server = ThreadingHTTPServer((self._host, self._port), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()


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
                    self._run.run_id,
                    now=identity.now(),
                    lease=self._lease,
                    # Without this the answer is about the *run*, not about this
                    # worker's claim on it: a superseded worker matched the
                    # `running` row the new owner had just renewed, was told
                    # `True`, and went on extending somebody else's lease.
                    worker_id=self._run.worker_id,
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
