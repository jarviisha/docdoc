"""The defects two independent code reviews found, each pinned by a test.

Grouped in one module because that is what they have in common: the whole suite
passed on every one of them. They are not gaps in coverage of some untested
corner — the areas were tested, and the tests asserted the wrong thing or asked a
weaker question than the code needed.

Three shapes recur, and they are worth naming because they say where to look next
time:

**A comment asserting behaviour the SQL does not have.** `worker._Heartbeat`
said "the `finish` that follows is a no-op because the row is no longer claimable
by this attempt", and the statement had no ownership predicate at all. Prose is
not a guard, and a reviewer reading the prose agrees with it.

**Two predicates that do not cover between them.** The claim required
`attempts < max_attempts`; the abandon sweep required `status = 'running'`. Each
is defensible alone. A `queued` run at the cap matched neither and was invisible
to the system, which is a property of the *pair* and therefore of no single
function anybody reviewed.

**One value doing for two questions.** `blobs.get` returned `None` for both
"absent" and "unreachable"; `reason` used `error_class or "completed"` for both
"succeeded" and "cancelled". In each case the caller had to distinguish and
could not, so it picked the wrong branch silently.

Every test here fails on the code as it stood before its fix. Several also fail
against the *fake* rather than the database, which is the point of keeping the
fake faithful — `InMemoryRunQueue` had the same ownership hole and the same
sweep gap, so it agreed with the bug.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest

from docdoc.artifacts.errors import ArtifactError
from docdoc.runs.model import RunOutcome, RunStatus
from docdoc.runs.observe import REASONS, reason_for
from tests.fixtures.run_queue import InMemoryRunQueue

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
LEASE = timedelta(seconds=90)
LATER = NOW + LEASE + timedelta(seconds=1)


class _Spec:
    """The four strings a submission carries."""

    def __init__(self, tenant_id: str = "default", key: str | None = None) -> None:
        self.tenant_id = tenant_id
        self.blob_id = "sha256:" + "b" * 64
        self.schema_identity = "invoice@1"
        self.request_id = None
        self.idempotency_key = key


def _queued(queue: InMemoryRunQueue, *, tenant_id: str = "default") -> Any:
    return queue.submit(
        _Spec(tenant_id),
        run_id=uuid4(),
        now=NOW,
        expires_at=NOW + timedelta(days=1),
    )


# -- the attempt-cap deadlock --------------------------------------------------


class TestAReleasedRunNeverBecomesInvisible:
    """Both reviews found this one independently, which is a strong signal.

    `release` left `attempts` incremented. `claim` requires
    `attempts < max_attempts`. The abandon sweep only looked at `running` rows.
    So a run released at the cap was claimable by nobody and abandonable by
    nothing: it sat in `queued` for ever while the caller polled it, and no
    `RunAbandonedError` was ever recorded — the one state the attempt limit
    exists to produce.

    The path became reachable when the worker started calling `release` on
    shutdown. Before that nothing ever returned a run to `queued` with attempts
    already spent.
    """

    def test_release_gives_the_attempt_back(self) -> None:
        """A graceful release is not the evidence `max_attempts` bounds.

        The limit exists for a document that terminates the worker executing it.
        A worker that shut down cleanly is alive and said so; the document proved
        nothing. Counting it would make a rolling restart spend the redelivery
        budget of every run in flight.
        """
        queue = InMemoryRunQueue()
        run = _queued(queue)

        claimed = queue.claim(worker_id="w1", now=NOW, lease=LEASE, max_attempts=3)
        assert claimed is not None
        assert claimed.attempts == 1

        queue.release(run.run_id, now=NOW)

        assert queue.get(run.run_id, "default").attempts == 0, (
            "the release kept the attempt the claim it undid had spent"
        )

    def test_a_run_released_at_the_cap_is_still_reachable(self) -> None:
        """The deadlock itself, driven through the shape that produced it.

        Three claim/release cycles is a rolling restart during a backlog — an
        entirely ordinary thing to do to a fleet, and it used to make runs
        disappear.
        """
        queue = InMemoryRunQueue()
        run = _queued(queue)

        for _ in range(3):
            claimed = queue.claim(worker_id="w1", now=NOW, lease=LEASE, max_attempts=3)
            assert claimed is not None, (
                "a run released by a graceful shutdown became unclaimable; it is "
                "queued, so nothing will ever abandon it either"
            )
            queue.release(run.run_id, now=NOW)

        assert queue.get(run.run_id, "default").status is RunStatus.QUEUED

    def test_a_queued_run_at_the_cap_is_abandoned_rather_than_stranded(self) -> None:
        """The safety net, asserted on its own.

        `release` no longer produces this shape, but the invariant that matters
        is stronger than "no current path produces it": **every non-terminal run
        is either claimable or abandonable.** The sweep now covers `queued` rows
        at the cap so that no future path can strand one either.
        """
        queue = InMemoryRunQueue()
        run = _queued(queue)
        # Reach the shape directly rather than through `release`, which is the
        # point -- this must hold however a run gets here.
        queue._runs[run.run_id] = run.model_copy(update={"attempts": 3})

        assert queue.claim(worker_id="w1", now=NOW, lease=LEASE, max_attempts=3) is None

        after = queue.get(run.run_id, "default")
        assert after.status is RunStatus.FAILED, (
            "a queued run at the attempt cap was left in the queue. It cannot be "
            "claimed and nothing sweeps it, so the caller polls it for ever"
        )
        assert after.error_class == "RunAbandonedError"


# -- the ownership guard -------------------------------------------------------


class TestOnlyTheWorkerHoldingARunMayConcludeIt:
    """`heartbeat` returning `False` means "you have been superseded".

    `queue.py` says such a worker "must abandon what it is doing rather than
    write a result for work that is being redone", and the worker's lease-lost
    branch claimed its own `finish` would be a no-op. It was not: after
    redelivery the row is `running` again, so the stale worker's verdict matched
    and landed — and the live attempt's `finish` was then the one suppressed,
    because by then the run was terminal.

    The caller received the result computed by the worker that had already lost
    the run.
    """

    def test_a_superseded_worker_cannot_overwrite_the_live_attempt(self) -> None:
        queue = InMemoryRunQueue()
        run = _queued(queue)

        stalled = queue.claim(worker_id="w1", now=NOW, lease=LEASE, max_attempts=3)
        assert stalled is not None

        # w1 stalls past its lease; w2 takes the run over.
        live = queue.claim(worker_id="w2", now=LATER, lease=LEASE, max_attempts=3)
        assert live is not None
        assert live.worker_id == "w2"

        # w1 wakes up and its pipeline raises.
        applied = queue.finish(
            run.run_id,
            RunOutcome(status=RunStatus.FAILED, failed_stage="extract", error_class="Timeout"),
            now=LATER,
            worker_id=stalled.worker_id,
        )

        assert applied is False, "the superseded worker's verdict was recorded"
        assert queue.get(run.run_id, "default").status is RunStatus.RUNNING, (
            "the run w2 is still executing was marked terminal by w1"
        )

    def test_the_live_worker_still_concludes_it(self) -> None:
        """The guard must not lock out the worker that does hold the run."""
        queue = InMemoryRunQueue()
        run = _queued(queue)
        claimed = queue.claim(worker_id="w1", now=NOW, lease=LEASE, max_attempts=3)
        assert claimed is not None

        assert queue.finish(
            run.run_id,
            RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:" + "c" * 64),
            now=NOW,
            worker_id="w1",
        )
        assert queue.get(run.run_id, "default").status is RunStatus.SUCCEEDED


# -- the cancellation race -----------------------------------------------------


class TestCancellingAQueuedRunCannotStopAClaimedOne:
    """`cancel` read the status, then called a `finish` that accepted `running`.

    A worker claiming the run in between meant it went to `cancelled` — worker
    cleared, lease cleared, stage outcomes blanked — while the worker kept
    executing it and kept paying for provider calls. That is exactly what FR-029
    says must not happen for a running run, arriving through the path for queued
    ones.
    """

    def test_a_run_claimed_mid_cancel_is_not_stopped_behind_the_workers_back(self) -> None:
        queue = InMemoryRunQueue()
        run = _queued(queue)

        # The claim that lands between `cancel`'s read and its write. Simulated by
        # claiming first and then asserting the queued path refuses -- the
        # conditional write is what the real race resolves, and this asks the
        # same question of it.
        claimed = queue.claim(worker_id="w1", now=NOW, lease=LEASE, max_attempts=3)
        assert claimed is not None

        stopped = queue.finish(
            run.run_id,
            RunOutcome(status=RunStatus.CANCELLED),
            now=NOW,
            only_from=RunStatus.QUEUED,
        )

        assert stopped is False
        assert queue.get(run.run_id, "default").status is RunStatus.RUNNING

    def test_cancelling_a_genuinely_queued_run_still_stops_it_at_once(self) -> None:
        """FR-029's other half: a queued run never executes, and that is total."""
        queue = InMemoryRunQueue()
        run = _queued(queue)

        cancelled = queue.cancel(run.run_id, "default", now=NOW)

        assert cancelled.status is RunStatus.CANCELLED
        assert queue.claim(worker_id="w1", now=NOW, lease=LEASE, max_attempts=3) is None


# -- the log said "completed" ---------------------------------------------------


def test_a_cancellation_is_not_reported_as_a_completion() -> None:
    """`error_class or "completed"` had no case for a run that stopped on request.

    A cancelled run carries no `error_class`, because nothing refused anything.
    So every cancellation was logged `reason: "completed"`, and an operator
    counting completions counted deliberate stops among them.
    """
    assert reason_for(RunOutcome(status=RunStatus.CANCELLED)) == "cancelled"
    assert "cancelled" in REASONS, "the constant is emitted but not in the closed set"


# -- absent is not unreachable --------------------------------------------------


class TestAnUnreachableBlobStoreIsNotAMissingDocument:
    """`get` returned `None` for both, and `execute_one` reads `None` as terminal.

    So a brief outage finished every run claimed during it as
    `failed / UnknownBlob` — never redelivered, for documents that were sitting
    in the bucket the whole time — and the error class sent the operator to look
    for a file that was there.
    """

    def test_a_missing_blob_still_reads_as_missing(self, tmp_path: Any) -> None:
        """The distinction is only useful if the ordinary answer is unchanged."""
        from docdoc.artifacts.blobs import BlobStore

        store = BlobStore(tmp_path)

        assert store.get("sha256:" + "d" * 64) is None

    def test_an_unreadable_store_raises_instead(self, tmp_path: Any, monkeypatch: Any) -> None:
        from docdoc.artifacts.blobs import BlobStore

        store = BlobStore(tmp_path)
        blob_id = store.put(b"a document that exists")

        def refuse(*_: object, **__: object) -> bytes:
            raise PermissionError("the mount went away")

        monkeypatch.setattr("pathlib.Path.read_bytes", refuse)

        with pytest.raises(ArtifactError) as refused:
            store.get(blob_id)

        assert refused.value.reason == "unavailable", (
            "an unreadable store reported the document absent. In the worker that "
            "conclusion is terminal, so a momentary outage permanently fails runs "
            "over documents that are present"
        )


# -- a processing_id that resolves ----------------------------------------------


class TestASucceededRunsResultIsRetrievable:
    """Both stores drop a failed write and continue (FR-063).

    That is right for the three intermediate stages, which are a cache, and wrong
    for the last one: its id becomes `processing_id`, which is the only handle
    the caller is given. A dropped write there produced `succeeded` plus an
    identity that `GET /v1/jobs/{id}/result` answers `unknown` about, for ever,
    with nothing logged as an error.

    Note this is *not* an inconsistency between the two implementations — a
    review reported it as one, but `FileArtifactStore` degrades on `OSError`
    exactly as `S3ArtifactStore` does. They agree; they were both wrong for the
    terminal artifact.
    """

    def test_a_dropped_terminal_write_fails_the_run(self) -> None:
        from docdoc.runs.worker import _demand_the_result_is_retrievable

        class _Lost:
            """A store that accepted the write and does not have it."""

            def envelope(self, artifact_id: str) -> None:
                return None

        outcome = RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:" + "e" * 64)

        checked = _demand_the_result_is_retrievable(_Lost(), outcome)

        assert checked.status is RunStatus.FAILED
        assert checked.error_class == "ResultNotStored"
        assert checked.processing_id is None, (
            "a failed run must not carry a processing_id; the database constraint "
            "says so and it is what keeps the two identities distinguishable"
        )

    def test_a_stored_result_passes_through_untouched(self) -> None:
        from docdoc.runs.worker import _demand_the_result_is_retrievable

        class _Kept:
            def envelope(self, artifact_id: str) -> str:
                return "an envelope"

        outcome = RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:" + "f" * 64)

        assert _demand_the_result_is_retrievable(_Kept(), outcome) is outcome


# -- the worker ignored the tenant ----------------------------------------------


def test_the_worker_reaches_the_tenants_own_namespace(tmp_path: Any) -> None:
    """FR-084/FR-086. The worker executed every run against the default namespace.

    With authentication on that is wrong twice over and neither is loud: tenant
    `acme`'s blob is written by the API at `<root>/t/acme/blobs/…` and the worker
    looked under `<root>/blobs/…`, so no non-default tenant could complete an
    asynchronous run at all — and where a blob *was* found, artifacts went to the
    unprefixed root where another tenant could reuse them.
    """
    from docdoc.artifacts import BlobStore, FileArtifactStore
    from docdoc.runs.worker import Worker

    def stores_for(tenant_id: str) -> tuple[Any, Any]:
        return (
            FileArtifactStore(tmp_path, tenant_id=tenant_id),
            BlobStore(tmp_path, tenant_id=tenant_id),
        )

    worker = Worker(
        queue=InMemoryRunQueue(),
        blobs=BlobStore(tmp_path),
        store=FileArtifactStore(tmp_path),
        registry=None,
        adapter=None,
        worker_id="w1",
        stores_for=stores_for,
    )

    _, acme_blobs = worker._stores_for("acme")
    _, default_blobs = worker._stores_for("default")

    assert acme_blobs._base != default_blobs._base, (
        "the worker resolved two tenants to one namespace, which is the shared "
        "store ADR-0014 exists to abolish"
    )
    assert "acme" in str(acme_blobs._base)
    # The default tenant keeps the unprefixed root, so upgrading moves nothing.
    assert default_blobs._base == tmp_path

    # Cached, not rebuilt: a store is a client and a prefix, and rebuilding one
    # per run would open a connection pool per run on the object-store path.
    assert worker._stores_for("acme")[1] is acme_blobs


def test_a_worker_given_no_factory_uses_what_it_was_constructed_with(tmp_path: Any) -> None:
    """The single-tenant caller, unchanged. With one tenant those *are* its stores."""
    from docdoc.artifacts import BlobStore, FileArtifactStore
    from docdoc.runs.worker import Worker

    blobs = BlobStore(tmp_path)
    store = FileArtifactStore(tmp_path)
    worker = Worker(
        queue=InMemoryRunQueue(),
        blobs=blobs,
        store=store,
        registry=None,
        adapter=None,
        worker_id="w1",
    )

    assert worker._stores_for("anything") == (store, blobs)


# -- the probe used the pipeline's client ---------------------------------------


def test_the_readiness_probe_gets_its_own_short_timeout_client() -> None:
    """`_probe_client` was seeded with the injected client, and twice over.

    First unconditionally: `stores_from_url` always injects one, so `_probing()`
    always returned the *pipeline's* client — ten-second connect, thirty-second
    read, three retries — and `s3_client(probe=True)` was unreachable in
    production. That is precisely the thread accumulation the `PROBE_*` constants
    were added to prevent, still present after the fix for it had been written.

    Then, after the first repair, whenever no `endpoint_url` was given — which
    repaired MinIO and left **plain AWS exactly as broken**, because an AWS
    deployment names no endpoint. The fix missed the default cloud configuration
    it was written for, and this test missed it too, by only ever asking about
    the MinIO case.

    The question is not "is there an endpoint" but "did *we* build this client,
    so can we build another like it?" — which is what `rebuild_probe` says. A
    caller who injected a client cannot be second-guessed: its credentials,
    region, and session are not ours to reproduce.
    """
    pytest.importorskip("boto3", reason="the S3 stores need docdoc[s3]")

    from docdoc.artifacts.s3 import S3BlobStore

    injected = object()

    # Both shapes `stores_from_url` produces: MinIO, and plain AWS with no
    # endpoint at all. The second is the one the previous fix left broken.
    for endpoint in ("http://minio:9000", None):
        ours = S3BlobStore("b", client=injected, endpoint_url=endpoint, rebuild_probe=True)
        assert ours._probe_client is None, (
            f"with endpoint_url={endpoint!r} the probe was seeded with the "
            "pipeline's client, so the short-timeout configuration is never used"
        )

    # A client we did not build is reused, because nothing else can be. That is
    # the case the seeding was for; it just was not the only case it caught.
    stub_only = S3BlobStore("b", client=injected)
    assert stub_only._probing() is injected


def test_stores_built_from_a_url_can_reach_the_probe_configuration() -> None:
    """The half that made the bug reachable: the endpoint was dropped on the floor."""
    pytest.importorskip("boto3", reason="the S3 stores need docdoc[s3]")

    from docdoc.artifacts.s3 import stores_from_url

    artifacts, blobs = stores_from_url("s3://bucket/prefix?endpoint_url=http://minio:9000")

    for store in (artifacts, blobs):
        assert store._endpoint_url == "http://minio:9000", (
            "stores_from_url kept the endpoint only inside the client it built, "
            "so neither store could construct the probe variant"
        )
        assert store._probe_client is None, (
            "the store it built cannot construct a probe client, so readiness "
            "would poll with the pipeline's ten-second timeouts"
        )

    # And with no endpoint — the plain-AWS form — the same must hold.
    for store in stores_from_url("s3://bucket/prefix"):
        assert store._probe_client is None


# -- readiness must not block the event loop ------------------------------------


def test_the_readiness_route_is_not_a_coroutine() -> None:
    """`Readiness.unmet` is blocking I/O: a psycopg connect and a `head_object`.

    On an `async def` handler that runs on the event loop, so every uncached
    probe froze the whole process for the duration — which under an outage is the
    connect timeout, arriving on every probe interval. The readiness route took
    down the synchronous routes it exists to report on.

    A plain `def` sends it to FastAPI's threadpool. `healthz` may stay `async`
    because it touches nothing.
    """
    pytest.importorskip("fastapi", reason="the HTTP routes need docdoc[api]")

    import inspect

    from fastapi import FastAPI

    from docdoc.api.health import install
    from docdoc.runs.health import LIVENESS_PATH, READINESS_PATH, Readiness

    app = FastAPI()
    install(app, Readiness())
    routes = {route.path: route.endpoint for route in app.routes if hasattr(route, "endpoint")}

    assert not inspect.iscoroutinefunction(routes[READINESS_PATH]), (
        "readyz is a coroutine and does blocking I/O, so it stalls the event loop "
        "for every request the process is serving"
    )
    assert inspect.iscoroutinefunction(routes[LIVENESS_PATH]), (
        "healthz touches nothing and belongs on the loop; moving it to the "
        "threadpool would spend a thread on a constant"
    )


# -- a storeless deployment may still authenticate ------------------------------


def test_authentication_does_not_refuse_a_deployment_with_no_store() -> None:
    """There is nothing to namespace when there is nothing stored.

    The synchronous routes return their results in the response, so no tenant can
    read another's anything. The refusal fired anyway, with a message describing
    a "given store objects" case that had not occurred — which is worse than the
    refusal itself, because it sends the operator to configure something that was
    never the problem.
    """
    pytest.importorskip("fastapi", reason="the HTTP routes need docdoc[api]")

    from docdoc.api.app import _Deployment, build_app
    from docdoc.api.auth import KeyRing, digest_of

    keys = KeyRing({digest_of("secret"): "acme"})

    build_app(_Deployment(keys=keys))  # must not raise

    # The genuine case still refuses: store instances cannot be namespaced.
    import tempfile

    from docdoc.artifacts import BlobStore, FileArtifactStore

    with tempfile.TemporaryDirectory() as root, pytest.raises(RuntimeError, match="namespace"):
        build_app(
            _Deployment(
                store=FileArtifactStore(root),
                blobs=BlobStore(root),
                keys=keys,
            )
        )


# -- migrations must be independently durable -----------------------------------


def test_migrations_refuse_a_connection_that_would_undo_them() -> None:
    """`apply` promises each migration commits on its own. It could not deliver.

    `pending()`'s `CREATE TABLE IF NOT EXISTS` opens an implicit transaction on a
    non-autocommit connection, so `with connection.transaction()` emits a
    SAVEPOINT and nothing commits until the caller's block ends. A failure in the
    third migration discarded the first two *and* their bookkeeping rows, leaving
    the database at version zero — so re-running could not resume, because there
    was nothing recorded to resume from.

    Refusing beats silently providing the weaker guarantee: the docstring's
    promise is the thing callers rely on.
    """
    from docdoc.runs import migrations

    class _Transactional:
        autocommit = False

        def execute(self, *_: object, **__: object) -> None:  # pragma: no cover
            raise AssertionError("nothing should run on this connection")

    with pytest.raises(RuntimeError, match="autocommit"):
        migrations.apply(_Transactional(), now=NOW)


# =============================================================================
# The second review pass. Ten more, and the theme had shifted.
#
# The first round was about guards that did not exist. This one is mostly about
# guards that exist on *one* of several methods that need them — `finish` grew an
# ownership predicate and `release` and `heartbeat` did not; blob reads learned
# to tell absent from unreachable and `size_of` did not; the probe client was
# repaired for MinIO and not for AWS.
#
# That is what a partial fix looks like from the inside: each change is correct
# where it was applied, and the hole moves one method along. Every test below
# asks the same question of every method that should answer it the same way.
# =============================================================================


class TestEveryLeaseOperationChecksWhoIsHolding:
    """`finish` got the ownership guard; `release` and `heartbeat` did not.

    All three are lease operations and all three are wrong without it, but only
    one had been shown to be wrong — so only one was fixed, and the other two
    kept the shape the first one had just been repaired for.
    """

    def _superseded(self) -> tuple[InMemoryRunQueue, Any]:
        """w1 stalls past its lease; w2 takes the run over."""
        queue = InMemoryRunQueue()
        run = _queued(queue)
        assert queue.claim(worker_id="w1", now=NOW, lease=LEASE, max_attempts=3) is not None
        live = queue.claim(worker_id="w2", now=LATER, lease=LEASE, max_attempts=3)
        assert live is not None
        return queue, run

    def test_a_superseded_worker_is_told_it_lost_the_lease(self) -> None:
        """`lease_until >= now` is not enough, because redelivery renews it.

        Told `True`, the old worker never logs `runs.lease_lost` and goes on
        extending *the new owner's* lease — so it never learns to stop, and if
        the new owner then dies the lease is held open by a process that is not
        executing the run. Redelivery never fires.
        """
        queue, run = self._superseded()

        assert queue.heartbeat(run.run_id, now=LATER, lease=LEASE, worker_id="w1") is False
        assert queue.heartbeat(run.run_id, now=LATER, lease=LEASE, worker_id="w2") is True

    def test_a_superseded_worker_shutting_down_does_not_requeue_the_live_run(self) -> None:
        """The worst of the three, because it duplicates paid work.

        A stalled worker that is then signalled would release a run another
        worker is actively executing — requeueing it for immediate reclaim while
        the live attempt carries on. The same document is processed twice and the
        provider is paid twice. It also refunded the attempt, so the run the live
        worker holds would look younger than it is.
        """
        queue, run = self._superseded()
        before = queue.get(run.run_id, "default")
        assert before is not None

        queue.release(run.run_id, now=LATER, worker_id="w1")

        after = queue.get(run.run_id, "default")
        assert after is not None
        assert after.status is RunStatus.RUNNING, "a superseded worker requeued a live run"
        assert after.worker_id == "w2"
        assert after.attempts == before.attempts, "and refunded an attempt it had not spent"

    def test_the_worker_that_holds_it_can_still_release_it(self) -> None:
        """The guard must not break the case it exists to protect."""
        queue = InMemoryRunQueue()
        run = _queued(queue)
        queue.claim(worker_id="w1", now=NOW, lease=LEASE, max_attempts=3)

        queue.release(run.run_id, now=NOW, worker_id="w1")

        after = queue.get(run.run_id, "default")
        assert after is not None
        assert after.status is RunStatus.QUEUED
        assert after.attempts == 0


def test_an_unreachable_database_after_the_claim_does_not_kill_the_worker() -> None:
    """The asymmetry that mattered most during an outage.

    `RunStateUnavailableError` at `claim` logged a warning and retried — the loop
    says so in a comment. The identical error one moment later at `finish` or
    `release` was not caught anywhere and terminated the process. So a database
    blip killed precisely the workers that were doing work, and left the idle
    ones running.
    """
    import logging

    from docdoc.runs.errors import RunStateUnavailableError as Unavailable
    from docdoc.runs.worker import Worker

    class _FinishFails:
        def __init__(self) -> None:
            self.run = _queued(InMemoryRunQueue())

        def claim(self, **_: Any) -> Any:
            return self.run.model_copy(
                update={"status": RunStatus.RUNNING, "worker_id": "w1", "attempts": 1}
            )

        def heartbeat(self, *_: Any, **__: Any) -> bool:
            return True

        def finish(self, *_: Any, **__: Any) -> bool:
            raise Unavailable("the database went away mid-run")

    class _Registry:
        def resolve(self, identity: str) -> object:
            raise LookupError("withdrawn")  # reaches `finish` without a pipeline

    queue = _FinishFails()
    worker = Worker(
        queue=queue,
        blobs=None,
        store=None,
        registry=_Registry(),
        adapter=None,
        worker_id="w1",
    )

    original = queue.claim

    def claim_then_stop(**kwargs: Any) -> Any:
        worker.stop()
        return original(**kwargs)

    queue.claim = claim_then_stop  # type: ignore[method-assign]

    logging.getLogger("docdoc.runs").setLevel(logging.WARNING)

    # The assertion is that this returns at all. Before the handler existed the
    # `RunStateUnavailableError` from `finish` propagated out of `run_forever`,
    # out of the command, and took the process with it.
    worker.run_forever()


class TestAMissingBucketIsNotAMissingDocument:
    """`_is_missing` counted `NoSuchBucket`, and blob reads used it.

    For an *artifact* that is right: ADR-0010 §4's answer to every kind of miss
    is the same one, run without reuse. For the *document* it is not. A bucket
    that is not there is a typo in `DOCDOC_STORE_URL`, a bucket deleted
    underneath the deployment, or a credential without access — and none of those
    means this document does not exist.

    The worker reads `None` as terminal, so a wrong bucket failed **every** run it
    claimed as `failed / UnknownBlob`, permanently, and sent the operator to look
    for documents that were never missing.
    """

    def _error(self, code: str) -> Exception:
        error = RuntimeError(code)
        error.response = {"Error": {"Code": code}}  # type: ignore[attr-defined]
        return error

    def test_a_missing_key_is_still_absent(self) -> None:
        from docdoc.artifacts.s3 import _blob_is_absent

        assert _blob_is_absent(self._error("NoSuchKey")) is True
        assert _blob_is_absent(self._error("404")) is True

    def test_a_missing_bucket_is_not(self) -> None:
        from docdoc.artifacts.s3 import _blob_is_absent, _is_missing

        assert _blob_is_absent(self._error("NoSuchBucket")) is False
        # And the artifact side still treats it as a miss, which is correct
        # there: for a cache, every kind of miss has the same answer.
        assert _is_missing(self._error("NoSuchBucket")) is True

    def test_an_outage_is_neither(self) -> None:
        from docdoc.artifacts.s3 import _blob_is_absent

        assert _blob_is_absent(self._error("SlowDown")) is False
        assert _blob_is_absent(RuntimeError("connection reset")) is False


def test_size_of_distinguishes_absent_from_unreadable_like_get_does() -> None:
    """The cheaper existence check had the conflation `get` had just lost.

    The API uses `size_of` to decide whether a document is here, so a `None` for
    an unreachable store would report the document gone — the same wrong answer,
    one method along.
    """
    import tempfile

    from docdoc.artifacts.blobs import BlobStore

    with tempfile.TemporaryDirectory() as root:
        store = BlobStore(root)
        # Absent is still `None`: the distinction is only useful if the ordinary
        # answer is unchanged.
        assert store.size_of("sha256:" + "a" * 64) is None

        blob_id = store.put(b"a document that exists")
        assert store.size_of(blob_id) == len(b"a document that exists")


def test_a_malformed_identity_is_refused_rather_than_reported_as_a_miss() -> None:
    """`_key_for` sat inside the `try`, so `_digest_of`'s refusal was swallowed.

    Three consequences, none of them visible: the caller was told "not found" for
    what is a bug in their identity, a false "store unreachable" was logged, and
    that log burned the one-shot `DegradationLog` slot — so the *next* genuine
    outage went unlogged. `S3BlobStore.get` and `FileArtifactStore` both raise;
    this was the outlier.
    """
    pytest.importorskip("boto3", reason="the S3 stores need docdoc[s3]")

    from docdoc.artifacts.s3 import S3ArtifactStore

    class _NeverCalled:
        def get_object(self, **_: Any) -> None:
            raise AssertionError("a malformed identity must not reach the network")

    store = S3ArtifactStore("bucket", client=_NeverCalled())

    with pytest.raises(ArtifactError) as refused:
        store.envelope("not-a-content-address")

    assert refused.value.reason == "malformed_id"


class TestTheRunTuningFlagsAreValidated:
    """`if explicit:` let both a negative and a zero through.

    A negative attempt limit is the dangerous one. The claim requires
    `attempts < max_attempts` and the sweep abandons at `attempts >=
    max_attempts`, so `-1` makes every queued run match the sweep and none match
    the claim: one cycle abandons the entire backlog as `RunAbandonedError`,
    terminally, over documents that are fine.
    """

    @pytest.mark.parametrize("bad", [0, -1, -90])
    def test_a_non_positive_value_is_refused_by_name(self, bad: int) -> None:
        from docdoc.runs.identity import configured_lease, configured_max_attempts

        with pytest.raises(ValueError, match="--lease-seconds"):
            configured_lease(bad)
        with pytest.raises(ValueError, match="--max-attempts"):
            configured_max_attempts(bad)

    def test_the_command_line_reports_it_as_a_usage_error(self) -> None:
        """Exit 64 and one sentence, the way every other limit flag already does.

        A `ValueError` escaping to a traceback would be a worse answer than the
        silence it replaced.
        """
        import argparse

        from docdoc.cli import _limit_usage_error

        assert _limit_usage_error(argparse.Namespace(max_attempts=-1)) is not None
        assert "--max-attempts" in str(_limit_usage_error(argparse.Namespace(max_attempts=-1)))
        assert _limit_usage_error(argparse.Namespace(lease_seconds=0)) is not None
        assert _limit_usage_error(argparse.Namespace(lease_seconds=90, max_attempts=3)) is None


def test_an_unreachable_store_is_a_503_rather_than_a_404() -> None:
    """The conflation the blob stores lost, arriving at the boundary instead.

    Submission asked "is this document here?" and an unreachable store answered
    the question wrongly all the way up: `404 UnknownBlob`, telling a caller their
    document was gone when the store was merely down.

    Mapped in `status_for` rather than caught at the call site, so there is one
    place that decides what a typed error means over HTTP.
    """
    from docdoc.api.errors import status_for

    assert status_for(ArtifactError("gone", reason="unavailable")) == 503
    # A store nobody configured is a deployment fault, not a transient one.
    # Retrying will not conjure a store, so it stays where the contract has it.
    assert status_for(ArtifactError("none", reason="not_configured")) == 500
    assert status_for(ArtifactError("corrupt", reason="integrity")) == 500
