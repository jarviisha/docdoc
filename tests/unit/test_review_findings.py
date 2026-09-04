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
    """`_probe_client` was seeded with the injected client unconditionally.

    `stores_from_url` always injects one, so `_probing()` always returned the
    *pipeline's* client — ten-second connect, thirty-second read, three retries —
    and `s3_client(probe=True)` was unreachable in production. That is precisely
    the thread accumulation the `PROBE_*` constants were added to prevent, still
    present after the fix for it had been written.
    """
    pytest.importorskip("boto3", reason="the S3 stores need docdoc[s3]")

    from docdoc.artifacts.s3 import S3BlobStore

    injected = object()

    # A store built with an endpoint can rebuild, so the probe is its own client.
    with_endpoint = S3BlobStore("b", client=injected, endpoint_url="http://minio:9000")
    assert with_endpoint._probe_client is None, (
        "the probe was seeded with the pipeline's client, so the short-timeout "
        "configuration is never the one used"
    )

    # A stub with no endpoint has nothing to rebuild from, so it reuses it. That
    # is the case the seeding was for; it just was not the only case it caught.
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
