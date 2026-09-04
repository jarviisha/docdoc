"""The operator-facing half of Milestone 9, which had no tests at all.

`docdoc migrate` and `docdoc worker` are the two commands a deployment actually
runs, and between them they were 29% and 37% covered — the bodies of both `run()`
functions were entirely unexecuted by the suite. So was the precedence logic
behind `DOCDOC_RUN_LEASE_SECONDS` and `DOCDOC_RUN_MAX_ATTEMPTS`, and so were the
worker loop's three failure branches.

That is not an accident of where somebody stopped writing tests. Everything in
this milestone that *processes a document* got tested heavily, because that is
where the interesting behaviour is; everything that *starts a process* got
nothing, because starting a process looks like plumbing. It is plumbing that
fails at three in the morning on somebody else's deployment, and the failure
modes here are the unhelpful kind — a command that exits zero having done
nothing, a worker that binds a port it was not asked for, a tuning knob that
silently means something other than what was typed.

The database-backed cases carry the `postgres` marker and skip without one, the
same as every other test that needs infrastructure.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from docdoc.runs.errors import RunStateUnavailableError
from docdoc.runs.identity import (
    DEFAULT_LEASE,
    DEFAULT_MAX_ATTEMPTS,
    configured_lease,
    configured_max_attempts,
)

NOW = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)


class _Registry:
    """Resolves anything. The schema is not what these tests are about."""

    def resolve(self, identity: str) -> object:
        return object()


class _ClaimOnce:
    """Hands out one running run and records what was done to it."""

    def __init__(self) -> None:
        self.released: list[Any] = []
        self.finished: list[Any] = []
        import uuid

        from docdoc.runs.model import Run, RunStatus

        self.run = Run(
            run_id=uuid.uuid4(),
            tenant_id="acme",
            blob_id="sha256:" + "a" * 64,
            schema_identity="invoice@1",
            status=RunStatus.RUNNING,
            worker_id="w1",
            created_at=NOW,
            updated_at=NOW,
            expires_at=NOW + timedelta(days=1),
        )

    def claim(self, **_: Any) -> Any:
        return self.run

    def heartbeat(self, *_: Any, **__: Any) -> bool:
        return True

    def release(self, run_id: Any, **_: Any) -> None:
        self.released.append(run_id)

    def finish(self, run_id: Any, outcome: Any, **_: Any) -> bool:
        self.finished.append(outcome)
        return True


class _Unreachable:
    """A blob store that is there and cannot be reached."""

    def get(self, blob_id: str) -> bytes:
        from docdoc.artifacts.errors import ArtifactError

        raise ArtifactError("the mount went away", reason="unavailable")


class _Lying:
    """A store whose bytes are not what was written."""

    def get(self, blob_id: str) -> bytes:
        from docdoc.artifacts.errors import ArtifactError

        raise ArtifactError("content_id mismatch", reason="integrity")


def _args(**values: Any) -> argparse.Namespace:
    return argparse.Namespace(**values)


# -- the two tuning knobs ------------------------------------------------------


class TestLeaseAndAttemptsFollowTheStatedPrecedence:
    """FR-083: explicit argument, then environment, then default.

    Both knobs went through `_positive_int`, which was uncovered, and its
    behaviour is a decision rather than an implementation detail: a malformed
    value falls back to the default rather than refusing to start, because a
    worker that dies over a typo in a tuning knob turns a cosmetic mistake into
    an outage.
    """

    def test_the_default_applies_when_nothing_is_configured(self) -> None:
        assert configured_lease() == DEFAULT_LEASE
        assert configured_max_attempts() == DEFAULT_MAX_ATTEMPTS

    def test_the_environment_beats_the_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("DOCDOC_RUN_LEASE_SECONDS", "30")
        monkeypatch.setenv("DOCDOC_RUN_MAX_ATTEMPTS", "7")

        assert configured_lease() == timedelta(seconds=30)
        assert configured_max_attempts() == 7

    def test_the_explicit_argument_beats_the_environment(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("DOCDOC_RUN_LEASE_SECONDS", "30")
        monkeypatch.setenv("DOCDOC_RUN_MAX_ATTEMPTS", "7")

        assert configured_lease(45) == timedelta(seconds=45)
        assert configured_max_attempts(2) == 2

    @pytest.mark.parametrize("nonsense", ["", "   ", "abc", "0", "-5", "1.5", "9e9x"])
    def test_an_unreadable_value_falls_back_rather_than_refusing_to_start(
        self, monkeypatch: pytest.MonkeyPatch, nonsense: str
    ) -> None:
        """The decision, pinned. Neither knob can change what a run *produces*.

        That is what separates these from `Limits`, where a malformed value must
        be refused: a wrong lease costs redelivery latency, a wrong page cap
        changes the answer.
        """
        monkeypatch.setenv("DOCDOC_RUN_LEASE_SECONDS", nonsense)
        monkeypatch.setenv("DOCDOC_RUN_MAX_ATTEMPTS", nonsense)

        assert configured_lease() == DEFAULT_LEASE
        assert configured_max_attempts() == DEFAULT_MAX_ATTEMPTS


# -- docdoc migrate ------------------------------------------------------------


class TestTheMigrateCommandRefusesBeforeItConnects:
    """The paths that need no database, which are the ones an operator hits first."""

    def test_no_dsn_says_what_to_set_and_why_there_is_no_default(self) -> None:
        from docdoc.cli.commands import migrate

        settings = type("S", (), {"run_database_url": None})()

        with pytest.raises(RunStateUnavailableError) as refused:
            migrate.run(_args(), settings)  # type: ignore[arg-type]

        message = str(refused.value)
        assert "DOCDOC_RUN_DATABASE_URL" in message
        assert "--run-database-url" in message
        # The absence of a default is a decision and the message says so, because
        # a command that invented a database to write to would be making an
        # operator's choice for them.
        assert "no default" in message


@pytest.mark.postgres
class TestTheMigrateCommandAgainstADatabase:
    """The body of `run()`, which nothing executed.

    `docdoc migrate` is the one command a deployment pipeline gates on, and
    `--check`'s exit code is the thing it gates on. An exit code nothing asserts
    is an exit code that can quietly become zero.
    """

    @pytest.fixture
    def fresh(self) -> str:
        """A database with the schema removed, so `apply` has work to do."""
        psycopg = pytest.importorskip("psycopg")
        from tests.infra import require_database

        dsn = require_database()
        with psycopg.connect(dsn, autocommit=True) as connection:
            connection.execute(
                "DROP TABLE IF EXISTS runs, docdoc_schema_version, docdoc_default_tenant CASCADE"
            )
        return dsn

    def test_check_exits_non_zero_while_anything_is_pending(self, fresh: str) -> None:
        from docdoc.cli.commands.migrate import EXIT_PENDING, run

        settings = type("S", (), {"run_database_url": fresh})()
        rendering = run(_args(check=True), settings)  # type: ignore[arg-type]

        assert rendering.code == EXIT_PENDING, (
            "a rollout that starts workers against a database missing the table "
            "they need should stop before the workers do"
        )
        assert rendering.data["pending"]
        assert rendering.data["applied"] == []

    def test_check_applies_nothing(self, fresh: str) -> None:
        """`--check` is a question. A question that changed the schema would be
        the worst possible shape for a pipeline gate."""
        from docdoc.cli.commands.migrate import EXIT_PENDING, run

        settings = type("S", (), {"run_database_url": fresh})()
        run(_args(check=True), settings)  # type: ignore[arg-type]

        assert run(_args(check=True), settings).code == EXIT_PENDING  # type: ignore[arg-type]

    def test_applying_reports_the_versions_and_the_store_root_owner(self, fresh: str) -> None:
        from docdoc.cli.commands.migrate import run

        settings = type("S", (), {"run_database_url": fresh})()
        rendering = run(_args(), settings)  # type: ignore[arg-type]

        assert rendering.code == 0
        assert rendering.data["applied"] == ["0001_runs", "0002_default_tenant"]
        assert rendering.data["default_tenant"] == "default"
        assert any("store root belongs to tenant" in line for line in rendering.lines)

    def test_re_running_is_a_no_op_and_says_so(self, fresh: str) -> None:
        """FR-078: safe to re-run, because a deployment pipeline will."""
        from docdoc.cli.commands.migrate import run

        settings = type("S", (), {"run_database_url": fresh})()
        run(_args(), settings)  # type: ignore[arg-type]
        again = run(_args(), settings)  # type: ignore[arg-type]

        assert again.code == 0
        assert again.data["applied"] == []
        assert "nothing to apply" in again.lines
        assert run(_args(check=True), settings).code == 0  # type: ignore[arg-type]

    def test_moving_the_store_root_owner_is_refused_naming_both(self, fresh: str) -> None:
        """FR-089's real content, and the reason it raises rather than warns.

        The recorded value decides where every read looks, so moving it after
        content exists strands that content — and the symptom is not an error but
        correct answers plus a silent re-payment for every parse.
        """
        from docdoc.cli.commands.migrate import run
        from docdoc.runs.errors import TenantAssignmentError

        settings = type("S", (), {"run_database_url": fresh})()
        run(_args(default_tenant="acme"), settings)  # type: ignore[arg-type]

        with pytest.raises(TenantAssignmentError) as refused:
            run(_args(default_tenant="globex"), settings)  # type: ignore[arg-type]

        assert "acme" in str(refused.value)
        assert "globex" in str(refused.value)


# -- docdoc worker -------------------------------------------------------------


class TestTheWorkerCommandRefusesBeforeItStarts:
    """Both refusals exist because the alternative fails silently later."""

    def test_no_dsn_is_refused(self) -> None:
        from docdoc.cli.commands import worker

        settings = type("S", (), {"run_database_url": None, "has_store": True})()

        with pytest.raises(RunStateUnavailableError, match="DOCDOC_RUN_DATABASE_URL"):
            worker.run(_args(), settings)  # type: ignore[arg-type]

    def test_no_store_is_refused_because_it_could_reach_no_document(self) -> None:
        """The blobs a worker reads are written by the API's submission route.

        A worker with no store cannot reach the document at all and would fail
        every run it claimed — so this refusal is not about reuse being off, it
        is about the process being unable to do its job.
        """
        from docdoc.cli.commands import worker

        settings = type("S", (), {"run_database_url": "postgresql://x/y", "has_store": False})()

        with pytest.raises(RunStateUnavailableError) as refused:
            worker.run(_args(), settings)  # type: ignore[arg-type]

        assert "no store configured" in str(refused.value)
        assert "cannot reach the documents it claims" in str(refused.value)

    def test_the_worker_id_names_a_host_and_a_process(self) -> None:
        """Diagnostic only — nothing routes on it, and worker liveness is the
        lease rather than a registry. Under Docker the hostname is the container
        id, which is what an operator greps for."""
        from docdoc.cli.commands.worker import _worker_id

        host, _, pid = _worker_id().partition(":")

        assert host
        assert pid.isdigit()


# -- the worker loop -----------------------------------------------------------


class TestTheLoopSurvivesWhatItIsSupposedToSurvive:
    """Three branches, each of which keeps a process alive through an outage.

    None was executed by the suite. They matter more than most branches here
    because the alternative to surviving is a crash loop across the fleet during
    exactly the incident an operator is already handling.
    """

    def _worker(self, queue: Any, **kwargs: Any) -> Any:
        from docdoc.runs.worker import Worker

        return Worker(
            queue=queue,
            blobs=kwargs.pop("blobs", None),
            store=kwargs.pop("store", None),
            # A registry that resolves. `execute_one` resolves the schema before
            # it touches a store, so `None` here would fail the run for a
            # withdrawn schema and never reach the branch under test.
            registry=kwargs.pop("registry", _Registry()),
            adapter=None,
            worker_id="w1",
            **kwargs,
        )

    def _run_one_iteration(self, worker: Any, queue: Any) -> None:
        """Let the loop go round exactly once, then stop.

        `stop()` before `run_forever()` is not the same thing and does not work:
        the loop tests the flag *before* claiming, so a worker stopped first
        performs zero iterations and every assertion below would pass vacuously.
        The flag has to be set from inside the iteration being observed.
        """
        original = queue.claim

        def claim_then_stop(**kwargs: Any) -> Any:
            worker.stop()
            return original(**kwargs)

        queue.claim = claim_then_stop
        worker.run_forever()

    def test_an_unreachable_queue_is_logged_and_retried_rather_than_fatal(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Not fatal: it is exactly the state readiness reports and an operator is
        already fixing, and a worker that exited would need restarting after."""
        import logging

        class _Down:
            def __init__(self) -> None:
                self.asked = 0

            def claim(self, **_: Any) -> None:
                self.asked += 1
                raise RunStateUnavailableError("down")

        queue = _Down()
        worker = self._worker(queue)

        with caplog.at_level(logging.WARNING, logger="docdoc.runs"):
            self._run_one_iteration(worker, queue)

        assert queue.asked == 1
        assert any("queue_unavailable" in record.getMessage() for record in caplog.records)

    def test_a_store_outage_leaves_the_run_for_redelivery_instead_of_crashing(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The branch added for the blob-store finding.

        Deliberately *not* `release`: that would requeue the run instantly and
        this worker would claim it again immediately, spinning against a store
        that is still down. Waiting out the lease is the backoff.
        """
        import logging

        from docdoc.artifacts.errors import ArtifactError

        queue = _ClaimOnce()
        worker = self._worker(queue, blobs=_Unreachable(), store=None)

        with caplog.at_level(logging.WARNING, logger="docdoc.runs"):
            self._run_one_iteration(worker, queue)  # must not raise

        assert any("store_unavailable" in record.getMessage() for record in caplog.records)
        assert queue.released == [], (
            "the run was released, so this worker will reclaim it immediately and "
            "spin against a store that is still down"
        )
        assert queue.finished == [], "an outage is not a verdict; the run must stay claimable"
        assert ArtifactError  # the import is the contract being relied on

    def test_a_corrupt_artifact_is_not_slept_through(self) -> None:
        """The other half of that branch: `unavailable` backs off, everything
        else is a fault to surface. A store that is *lying* is not a store that
        is merely absent."""
        from docdoc.artifacts.errors import ArtifactError

        queue = _ClaimOnce()
        worker = self._worker(queue, blobs=_Lying(), store=None)

        with pytest.raises(ArtifactError) as surfaced:
            self._run_one_iteration(worker, queue)

        assert surfaced.value.reason == "integrity"

    def test_signal_handlers_only_set_a_flag(self) -> None:
        """Doing the work in a handler would run it on whatever stack the signal
        interrupted, which for a run mid-pipeline is every stack in the process."""
        import signal

        from tests.fixtures.run_queue import InMemoryRunQueue

        worker = self._worker(InMemoryRunQueue())
        worker.install_signal_handlers()
        try:
            for received in (signal.SIGTERM, signal.SIGINT):
                assert signal.getsignal(received) not in (
                    signal.SIG_DFL,
                    signal.SIG_IGN,
                ), f"{received} was not installed"

            handler = signal.getsignal(signal.SIGTERM)
            assert callable(handler)
            handler(signal.SIGTERM, None)  # type: ignore[operator]

            assert worker._stopping.is_set()
            # And the loop returns rather than claiming anything.
            worker.run_forever()
        finally:
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.signal(signal.SIGINT, signal.default_int_handler)


# -- the migration module's own edges ------------------------------------------


def test_discover_refuses_a_wheel_built_without_its_sql(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A packaging failure that would otherwise report "nothing to do".

    The alternative is a deployment with no table, no error, and a worker that
    fails every claim — which is a long way from the missing `.sql` files that
    caused it.
    """
    from docdoc.runs import migrations

    monkeypatch.setattr(migrations, "_HERE", tmp_path)

    with pytest.raises(RuntimeError, match="built without its"):
        migrations.discover()


def test_a_migration_reports_its_version_and_reads_its_own_sql(tmp_path: Path) -> None:
    from docdoc.runs.migrations import Migration

    path = tmp_path / "0003_something.sql"
    path.write_text("SELECT 1", encoding="utf-8")
    migration = Migration(path)

    assert migration.version == "0003_something"
    assert migration.sql == "SELECT 1"
    assert "0003_something" in repr(migration)


# -- the settings delegates ----------------------------------------------------


def test_the_http_settings_delegate_rather_than_reimplement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`docdoc.api` and `docdoc.cli` are declared independent of each other.

    So a precedence rule implemented in `api/settings.py` would be one the worker
    — a CLI subcommand — could not call, and the two would drift into two answers
    for one setting. These three delegate to the layer below both front ends, and
    this asserts they still delegate rather than having grown a second
    implementation that agrees today.
    """
    from docdoc.api import settings

    monkeypatch.setenv("DOCDOC_RUN_LEASE_SECONDS", "42")
    monkeypatch.setenv("DOCDOC_RUN_MAX_ATTEMPTS", "9")

    assert settings.run_lease() == configured_lease() == timedelta(seconds=42)
    assert settings.run_max_attempts() == configured_max_attempts() == 9
    assert settings.run_lease(5) == timedelta(seconds=5)
    assert settings.run_max_attempts(1) == 1


def test_the_store_url_delegate_reaches_the_same_builder() -> None:
    """Kept under its original name because the HTTP layer's callers use it, and
    implemented once because the worker needs the same thing."""
    pytest.importorskip("boto3", reason="object stores need docdoc[s3]")

    from docdoc.api.settings import store_from_url

    artifacts, blobs = store_from_url("s3://bucket/p?endpoint_url=http://minio:9000", tenant_id="a")

    assert artifacts.root == "s3://bucket/p/t/a"
    assert blobs.root == "s3://bucket/p/t/a"


# -- the worker command's happy path -------------------------------------------


def test_the_worker_command_builds_a_worker_and_runs_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The body nothing executed, with the loop replaced rather than entered.

    `run_forever` blocks until signalled — that is what the command is for — so a
    test that called it for real would hang. What is worth asserting is
    everything *around* it: that the queue is a `PostgresRunQueue`, that the
    per-tenant store factory is wired (without it the worker executes every run
    against the default namespace), that the tuning knobs arrive, and that
    signal handlers are installed before the loop starts rather than after.
    """
    pytest.importorskip("psycopg", reason="the worker needs docdoc[postgres]")

    from docdoc.cli.commands import worker as command
    from docdoc.runs.postgres import PostgresRunQueue

    built: dict[str, Any] = {}
    order: list[str] = []

    class _Worker:
        def __init__(self, **kwargs: Any) -> None:
            built.update(kwargs)

        def install_signal_handlers(self) -> None:
            order.append("handlers")

        def run_forever(self) -> None:
            order.append("loop")

    monkeypatch.setattr(command, "Worker", _Worker)

    class _Settings:
        run_database_url = "postgresql://docdoc:docdoc@localhost:1/docdoc"
        has_store = True

        def blobs(self) -> object:
            return "blobs"

        def store(self) -> object:
            return "store"

        def registry(self) -> object:
            return "registry"

        def adapter(self) -> object:
            return "adapter"

        def limits(self) -> object:
            return "limits"

        def stores_for(self, tenant_id: str) -> tuple[object, object]:
            return (f"store:{tenant_id}", f"blobs:{tenant_id}")

    rendering = command.run(
        _args(lease_seconds=30, max_attempts=5, health_port=8000),
        _Settings(),  # type: ignore[arg-type]
    )

    assert isinstance(built["queue"], PostgresRunQueue)
    assert built["lease"] == timedelta(seconds=30)
    assert built["max_attempts"] == 5
    assert built["health_port"] == 8000
    assert built["stores_for"]("acme") == ("store:acme", "blobs:acme"), (
        "the per-tenant store factory was not passed, so the worker would run "
        "every tenant's document against the default namespace"
    )
    assert order == ["handlers", "loop"], (
        "the loop started before signal handlers were installed, so a SIGTERM "
        "arriving in that window kills the process mid-run instead of draining it"
    )
    assert rendering.code == 0
    assert rendering.data == {"worker": "stopped"}


# -- the heartbeat thread ------------------------------------------------------


class TestTheHeartbeatThread:
    """The only thread a worker has, and both of its branches were uncovered.

    It exists because a lease sized to the slowest document would make every
    crash cost that long in redelivery latency, while a lease sized to the
    *heartbeat* costs ninety seconds regardless of how long the document takes.
    """

    def _beat_until(self, queue: Any, seen: Any) -> None:
        import time

        from docdoc.runs.worker import _Heartbeat

        beat = _Heartbeat(queue, _ClaimOnce().run, timedelta(seconds=0.06))
        beat.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and not seen():
            time.sleep(0.01)
        beat.stop()

    def test_it_extends_the_lease_while_the_run_is_executing(self) -> None:
        class _Live:
            def __init__(self) -> None:
                self.beats = 0

            def heartbeat(self, *_: Any, **__: Any) -> bool:
                self.beats += 1
                return True

        queue = _Live()
        self._beat_until(queue, lambda: queue.beats >= 1)

        assert queue.beats >= 1

    def test_a_database_blip_is_skipped_rather_than_ending_the_thread(self) -> None:
        """A heartbeat that gave up on the first failed round trip would drop a
        lease over a blip the run itself is surviving."""

        class _Flaky:
            def __init__(self) -> None:
                self.asked = 0

            def heartbeat(self, *_: Any, **__: Any) -> bool:
                self.asked += 1
                raise RunStateUnavailableError("blip")

        queue = _Flaky()
        self._beat_until(queue, lambda: queue.asked >= 2)

        assert queue.asked >= 2, "the thread stopped after one failure"

    def test_losing_the_lease_is_recorded_and_the_thread_returns(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Recorded and *not acted on*: this thread cannot safely interrupt a
        provider call. What stops the stale worker writing a verdict is the
        ownership guard on `finish`, not this."""
        import logging

        class _Superseded:
            def __init__(self) -> None:
                self.asked = 0

            def heartbeat(self, *_: Any, **__: Any) -> bool:
                self.asked += 1
                return False

        queue = _Superseded()
        with caplog.at_level(logging.WARNING, logger="docdoc.runs"):
            self._beat_until(queue, lambda: queue.asked >= 1)

        assert any("lease_lost" in record.getMessage() for record in caplog.records)


# -- execute_one's two never-reached-a-stage exits -----------------------------


def test_a_blob_that_is_genuinely_absent_fails_the_run_terminally() -> None:
    """Nothing will make it appear, so retrying is not the answer.

    The contrast with the unreachable case above is the whole point: `None` means
    absent and is terminal, an `ArtifactError` means unreachable and is not.
    """
    from docdoc.runs.model import RunStatus
    from docdoc.runs.worker import execute_one

    class _Empty:
        def get(self, blob_id: str) -> None:
            return None

    queue = _ClaimOnce()
    assert (
        execute_one(
            queue.run,
            queue=queue,
            blobs=_Empty(),
            store=None,
            registry=_Registry(),
            adapter=None,
            now=NOW,
        )
        is None
    )

    assert len(queue.finished) == 1
    assert queue.finished[0].status is RunStatus.FAILED
    assert queue.finished[0].error_class == "UnknownBlob"
    assert queue.finished[0].failed_stage is None, (
        "no stage refused anything; naming one would send a reader to the wrong code"
    )


def test_a_store_that_raises_while_confirming_the_result_counts_as_not_stored() -> None:
    """If the store cannot be reached now, the caller cannot fetch the result
    either, so reporting `succeeded` would describe a result nobody can get."""
    from docdoc.runs.model import RunOutcome, RunStatus
    from docdoc.runs.worker import _demand_the_result_is_retrievable

    class _Refuses:
        def envelope(self, artifact_id: str) -> None:
            raise RuntimeError("the bucket is not answering")

    checked = _demand_the_result_is_retrievable(
        _Refuses(),
        RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:" + "b" * 64),
    )

    assert checked.status is RunStatus.FAILED
    assert checked.error_class == "ResultNotStored"


# -- the last few branches, each of which is a decision ------------------------


def test_a_worker_given_a_port_serves_the_probes_and_stops_them_with_itself() -> None:
    """`Worker._serve_health`, as against `_HealthServer` which is tested alone.

    Port `0` so nothing races whatever else on the machine wants a fixed one —
    the flake that arrives on a busy CI runner and nowhere else. The pairing that
    matters is start-with-the-loop and stop-with-it: a daemon thread that
    outlived its worker would hold the port on restart.
    """
    import urllib.error
    import urllib.request

    from docdoc.runs.health import LIVENESS_PATH
    from docdoc.runs.worker import Worker
    from tests.fixtures.run_queue import InMemoryRunQueue

    worker = Worker(
        queue=InMemoryRunQueue(),
        blobs=None,
        store=None,
        registry=None,
        adapter=None,
        worker_id="w1",
        health_port=0,
    )
    ports: list[int] = []
    original = worker._claim_forever

    def note_the_port_then_stop() -> None:
        assert worker._health is not None
        ports.append(worker._health.bound_port)
        with urllib.request.urlopen(
            f"http://127.0.0.1:{ports[0]}{LIVENESS_PATH}", timeout=5
        ) as answered:
            assert answered.status == 200
        worker.stop()
        original()

    worker._claim_forever = note_the_port_then_stop  # type: ignore[method-assign]
    worker.run_forever()

    with pytest.raises(urllib.error.URLError):
        urllib.request.urlopen(f"http://127.0.0.1:{ports[0]}{LIVENESS_PATH}", timeout=5)


def test_an_unreachable_database_means_keep_going_rather_than_stop() -> None:
    """The cancellation read tolerates an outage, and the direction is deliberate.

    Treating a failed read as a cancellation would stop every running run in the
    fleet during a database blip — a far more expensive way to be wrong than
    finishing a run somebody asked to stop.
    """
    from docdoc.runs.worker import _cancellation_requested

    class _Down:
        def is_cancelled(self, run_id: Any) -> bool:
            raise RunStateUnavailableError("down")

    assert _cancellation_requested(_Down(), _ClaimOnce().run) is False


def test_readiness_reports_ready_as_a_boolean_too() -> None:
    """`is_ready` is what a caller that does not need the list of names asks."""
    from docdoc.runs.health import Readiness

    class _Up:
        def ping(self) -> None:
            return None

    class _Down:
        def ping(self) -> None:
            raise RuntimeError("no")

    assert Readiness(runs=_Up()).is_ready is True
    assert Readiness(runs=_Down()).is_ready is False
