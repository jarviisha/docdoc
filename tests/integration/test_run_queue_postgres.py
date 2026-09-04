"""`PostgresRunQueue` against a real database.

`tests/unit/test_claim_policy.py` already checks the *policy* against an
in-memory fake, and this file deliberately does not repeat it. What only a real
database can answer is whether `FOR UPDATE SKIP LOCKED` does what R8 claims when
two workers reach for the queue at the same instant — a question no fake can
pose, because the fake has no concurrency to lose.

Skips itself when `DOCDOC_TEST_DATABASE_URL` is unset, exactly as the
live-provider tests skip without credentials. `uv run pytest` on a machine with
no database passes and says what it skipped.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from tests.infra import require_database

from docdoc.runs import migrations
from docdoc.runs.errors import RunNotCancellableError, RunNotFoundError
from docdoc.runs.identity import new_run_id
from docdoc.runs.model import RunOutcome, RunStatus
from docdoc.runs.postgres import PostgresRunQueue

pytestmark = pytest.mark.postgres

LEASE = timedelta(seconds=90)


@dataclass
class Spec:
    tenant_id: str = "default"
    blob_id: str = "sha256:blob"
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None


@pytest.fixture
def queue() -> PostgresRunQueue:
    """A migrated, empty queue.

    Truncates rather than using a transaction-per-test, because the concurrency
    test below needs two real connections that can see each other's committed
    work — which a shared open transaction would prevent, and would prevent in a
    way that made `SKIP LOCKED` look like it worked.
    """
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()

    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs")

    return PostgresRunQueue(lambda: psycopg.connect(dsn))


def _submit(queue: PostgresRunQueue, *, at: datetime, **kwargs: object):
    run = queue.submit(
        Spec(**kwargs),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=at,
        expires_at=at + timedelta(days=30),
    )
    return run.run_id


def test_the_migration_is_idempotent(queue: PostgresRunQueue) -> None:
    """FR-078: safe to re-run, because a deployment pipeline will."""
    psycopg = pytest.importorskip("psycopg")
    with psycopg.connect(require_database(), autocommit=True) as connection:
        assert migrations.apply(connection, now=datetime.now(UTC)) == []
        assert migrations.pending(connection) == []


def test_two_workers_racing_never_receive_the_same_run(queue: PostgresRunQueue) -> None:
    """The question a fake cannot pose (FR-016).

    Ten runs, eight threads claiming as fast as they can. If `SKIP LOCKED` were
    absent the threads would serialise on the oldest row; if the claim were two
    statements instead of one, two of them would win the same run.
    """
    now = datetime.now(UTC)
    for offset in range(10):
        _submit(queue, at=now + timedelta(milliseconds=offset))

    def claim(worker: int):
        return queue.claim(
            worker_id=f"w{worker}", now=now + timedelta(minutes=1), lease=LEASE, max_attempts=3
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        claimed = [run for run in pool.map(claim, range(8)) if run is not None]

    ids = [run.run_id for run in claimed]
    assert len(ids) == 8, "eight workers, ten runs: every worker should get one"
    assert len(set(ids)) == 8, "two workers were handed the same run"


def test_a_locked_row_is_skipped_rather_than_waited_on(queue: PostgresRunQueue) -> None:
    """SKIP LOCKED's actual contract: the second worker takes the *next* run."""
    now = datetime.now(UTC)
    first = _submit(queue, at=now)
    second = _submit(queue, at=now + timedelta(seconds=1))

    a = queue.claim(worker_id="w1", now=now, lease=LEASE, max_attempts=3)
    b = queue.claim(worker_id="w2", now=now, lease=LEASE, max_attempts=3)

    assert a is not None
    assert b is not None
    assert a.run_id == first
    assert b.run_id == second


def test_the_check_constraint_refuses_a_failed_run_with_a_result(
    queue: PostgresRunQueue,
) -> None:
    """The invariant, enforced by the database and not only by the model.

    The model is not the only thing that can write this row — a migration, a
    repair script, or a future method could — so ADR-0013 §1's two identities are
    kept distinguishable at the level that no code path can bypass.
    """
    psycopg = pytest.importorskip("psycopg")
    now = datetime.now(UTC)
    run_id = _submit(queue, at=now)

    with (
        psycopg.connect(require_database()) as connection,
        pytest.raises(Exception, match="processing_id_belongs_to_success"),
    ):
        connection.execute(
            "UPDATE runs SET status = 'failed', processing_id = 'sha256:x' WHERE run_id = %s",
            (run_id,),
        )


def test_a_lapsed_lease_at_the_attempt_limit_is_abandoned_not_redelivered(
    queue: PostgresRunQueue,
) -> None:
    """FR-021 and SC-006, against the real claim query."""
    now = datetime.now(UTC)
    run_id = _submit(queue, at=now)

    at = now
    for _ in range(3):
        assert queue.claim(worker_id="w", now=at, lease=LEASE, max_attempts=3) is not None
        at += LEASE + timedelta(seconds=1)

    assert queue.claim(worker_id="w", now=at, lease=LEASE, max_attempts=3) is None

    run = queue.get(run_id, "default")
    assert run is not None
    assert run.status is RunStatus.FAILED
    assert run.error_class == "RunAbandonedError"


def test_idempotency_is_enforced_by_the_index_not_by_a_read(
    queue: PostgresRunQueue,
) -> None:
    """FR-011 under concurrency, which is the case the index exists for."""
    now = datetime.now(UTC)

    def submit(_: int):
        return queue.submit(
            Spec(idempotency_key="k1"),
            run_id=new_run_id(),
            now=now,
            expires_at=now + timedelta(days=30),
        ).run_id

    with ThreadPoolExecutor(max_workers=4) as pool:
        ids = set(pool.map(submit, range(4)))

    assert len(ids) == 1, "four concurrent retries of one key produced more than one run"


def test_the_same_key_under_two_tenants_is_two_runs(queue: PostgresRunQueue) -> None:
    now = datetime.now(UTC)
    a = _submit(queue, at=now, tenant_id="acme", idempotency_key="k1")
    b = _submit(queue, at=now, tenant_id="other", idempotency_key="k1")

    assert a != b


def test_stage_outcomes_survive_a_round_trip_through_jsonb(
    queue: PostgresRunQueue,
) -> None:
    """FR-036: a failed run keeps what the completed stages produced."""
    now = datetime.now(UTC)
    run_id = _submit(queue, at=now)
    queue.claim(worker_id="w1", now=now, lease=LEASE, max_attempts=3)

    queue.finish(
        run_id,
        RunOutcome(
            status=RunStatus.FAILED,
            failed_stage="extract",
            error_class="ProviderError",
            stage_outcomes=(
                {  # type: ignore[arg-type]
                    "stage": "parse",
                    "status": "executed",
                    "artifact_id": "sha256:p",
                    "duration_ms": 812,
                },
            ),
        ),
        now=now,
    )

    run = queue.get(run_id, "default")
    assert run is not None
    assert run.failed_stage == "extract"
    assert run.error_class == "ProviderError"
    assert len(run.stage_outcomes) == 1
    assert run.stage_outcomes[0].artifact_id == "sha256:p"
    assert run.stage_outcomes[0].duration_ms == 812


def test_finishing_a_terminal_run_does_not_overwrite_its_conclusion(
    queue: PostgresRunQueue,
) -> None:
    """A crash between the pipeline returning and `finish` committing is safe.

    The run is redelivered and the second attempt must not overwrite what the
    first one concluded — which is why the UPDATE is predicated on the run still
    being claimable.
    """
    now = datetime.now(UTC)
    run_id = _submit(queue, at=now)
    queue.claim(worker_id="w1", now=now, lease=LEASE, max_attempts=3)
    queue.finish(
        run_id, RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:first"), now=now
    )

    queue.finish(run_id, RunOutcome(status=RunStatus.FAILED, error_class="Late"), now=now)

    run = queue.get(run_id, "default")
    assert run is not None
    assert run.status is RunStatus.SUCCEEDED
    assert run.processing_id == "sha256:first"


# -- cancellation --------------------------------------------------------------


def test_cancelling_a_queued_run_makes_it_unclaimable(queue: PostgresRunQueue) -> None:
    now = datetime.now(UTC)
    run_id = _submit(queue, at=now)

    cancelled = queue.cancel(run_id, "default", now=now)

    assert cancelled.status is RunStatus.CANCELLED
    assert queue.claim(worker_id="w1", now=now, lease=LEASE, max_attempts=3) is None


def test_cancelling_a_running_run_records_the_request_and_still_reads_running(
    queue: PostgresRunQueue,
) -> None:
    """FR-029, against the column that carries it.

    `status` stays `running` because a provider call already in flight completes
    and is billed. The request lives in `cancel_requested`, which the worker
    reads at the next stage boundary — the column data-model.md originally
    omitted.
    """
    now = datetime.now(UTC)
    run_id = _submit(queue, at=now)
    queue.claim(worker_id="w1", now=now, lease=LEASE, max_attempts=3)

    run = queue.cancel(run_id, "default", now=now)

    assert run.status is RunStatus.RUNNING
    assert queue.is_cancelled(run_id) is True


def test_cancelling_a_terminal_run_is_refused_naming_the_state(
    queue: PostgresRunQueue,
) -> None:
    now = datetime.now(UTC)
    run_id = _submit(queue, at=now)
    queue.claim(worker_id="w1", now=now, lease=LEASE, max_attempts=3)
    queue.finish(run_id, RunOutcome(status=RunStatus.SUCCEEDED, processing_id="x"), now=now)

    with pytest.raises(RunNotCancellableError, match="succeeded"):
        queue.cancel(run_id, "default", now=now)


def test_cancelling_another_tenants_run_is_indistinguishable_from_a_stranger(
    queue: PostgresRunQueue,
) -> None:
    """FR-066: the same error for "not yours" and "never existed"."""
    now = datetime.now(UTC)
    run_id = _submit(queue, at=now, tenant_id="acme")

    with pytest.raises(RunNotFoundError):
        queue.cancel(run_id, "other", now=now)
    with pytest.raises(RunNotFoundError):
        queue.cancel(new_run_id(), "other", now=now)


# -- the review findings, against the SQL that carries them --------------------
#
# `tests/unit/test_review_findings.py` asks these of `InMemoryRunQueue`, which is
# the right place for the *policy*. Every one of them was a defect in a SQL
# predicate, though, and a fake cannot be wrong in the way a `WHERE` clause is
# wrong -- so the statements themselves are checked here, against a database.


def test_release_returns_the_attempt_it_undid(queue: PostgresRunQueue) -> None:
    """`GREATEST(attempts - 1, 0)`, and why the row must not be left at the cap.

    A rolling restart releases every in-flight run. Counting those releases
    against `max_attempts` spent the redelivery budget of runs that had met no
    fault at all.
    """
    at = datetime(2026, 3, 1, tzinfo=UTC)
    run_id = _submit(queue, at=at)

    claimed = queue.claim(worker_id="w1", now=at, lease=LEASE, max_attempts=3)
    assert claimed is not None
    assert claimed.attempts == 1

    queue.release(run_id, now=at)

    after = queue.get(run_id, "default")
    assert after is not None
    assert after.status is RunStatus.QUEUED
    assert after.attempts == 0


def test_a_queued_run_at_the_attempt_cap_does_not_vanish(queue: PostgresRunQueue) -> None:
    """The deadlock, driven through the statements that produced it.

    Three graceful releases used to leave `attempts = 3` on a `queued` row. The
    claim requires `attempts < max_attempts` and the sweep only touched `running`
    rows, so the run matched neither predicate and no worker or sweep could ever
    see it again.
    """
    at = datetime(2026, 3, 1, tzinfo=UTC)
    run_id = _submit(queue, at=at)

    for _ in range(4):
        claimed = queue.claim(worker_id="w1", now=at, lease=LEASE, max_attempts=3)
        assert claimed is not None, "a released run became invisible to the queue"
        queue.release(run_id, now=at)

    after = queue.get(run_id, "default")
    assert after is not None
    assert after.status is RunStatus.QUEUED


def test_the_sweep_also_reaches_a_stranded_queued_run(queue: PostgresRunQueue) -> None:
    """The safety net, reached directly rather than through `release`.

    The invariant is stronger than "no current path strands one": **every
    non-terminal run is either claimable or abandonable.** Asserted by putting a
    row into the stranded shape by hand, which is what a future path would do.
    """
    psycopg = pytest.importorskip("psycopg")
    at = datetime(2026, 3, 1, tzinfo=UTC)
    run_id = _submit(queue, at=at)

    with psycopg.connect(require_database(), autocommit=True) as connection:
        connection.execute("UPDATE runs SET attempts = 3 WHERE run_id = %s", (run_id,))

    assert queue.claim(worker_id="w1", now=at, lease=LEASE, max_attempts=3) is None

    after = queue.get(run_id, "default")
    assert after is not None
    assert after.status is RunStatus.FAILED
    assert after.error_class == "RunAbandonedError"


def test_a_superseded_worker_cannot_write_over_the_live_attempt(
    queue: PostgresRunQueue,
) -> None:
    """The ownership predicate, which the statement did not have.

    `worker._Heartbeat` asserted in a comment that a superseded worker's `finish`
    would be a no-op "because the row is no longer claimable by this attempt".
    The `WHERE` clause checked only the status, and after redelivery the status
    is `running` again — so the stale verdict matched, landed, and made the live
    attempt's own `finish` the one that was suppressed.
    """
    at = datetime(2026, 3, 1, tzinfo=UTC)
    later = at + LEASE + timedelta(seconds=1)
    run_id = _submit(queue, at=at)

    stalled = queue.claim(worker_id="w1", now=at, lease=LEASE, max_attempts=3)
    assert stalled is not None
    live = queue.claim(worker_id="w2", now=later, lease=LEASE, max_attempts=3)
    assert live is not None
    assert live.worker_id == "w2"

    applied = queue.finish(
        run_id,
        RunOutcome(status=RunStatus.FAILED, failed_stage="extract", error_class="Timeout"),
        now=later,
        worker_id="w1",
    )

    assert applied is False
    current = queue.get(run_id, "default")
    assert current is not None
    assert current.status is RunStatus.RUNNING, (
        "the run w2 is still executing was concluded by the worker that lost it"
    )

    # And w2, which does hold it, still can.
    assert queue.finish(
        run_id,
        RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:" + "a" * 64),
        now=later,
        worker_id="w2",
    )


def test_cancelling_cannot_stop_a_run_claimed_a_moment_earlier(
    queue: PostgresRunQueue,
) -> None:
    """`only_from`, which closes `cancel`'s time-of-check-to-time-of-use window.

    `cancel` read the status and then called a `finish` that accepted `running`.
    A claim landing in between meant the run went to `cancelled` — worker and
    lease cleared, outcomes blanked — while the worker kept executing it and kept
    paying for provider calls, which is what FR-029 forbids for a running run.
    """
    at = datetime(2026, 3, 1, tzinfo=UTC)
    run_id = _submit(queue, at=at)

    claimed = queue.claim(worker_id="w1", now=at, lease=LEASE, max_attempts=3)
    assert claimed is not None

    stopped = queue.finish(
        run_id,
        RunOutcome(status=RunStatus.CANCELLED),
        now=at,
        only_from=RunStatus.QUEUED,
    )

    assert stopped is False
    current = queue.get(run_id, "default")
    assert current is not None
    assert current.status is RunStatus.RUNNING


def test_cancelling_a_running_run_leaves_the_worker_holding_it(
    queue: PostgresRunQueue,
) -> None:
    """The end-to-end shape of the same thing, through the public method.

    The run keeps its `worker_id` and its lease. Before the fix a `cancel` racing
    a claim cleared both, so the worker executing the document held a lease on a
    row that said nobody had it.
    """
    at = datetime(2026, 3, 1, tzinfo=UTC)
    run_id = _submit(queue, at=at)
    claimed = queue.claim(worker_id="w1", now=at, lease=LEASE, max_attempts=3)
    assert claimed is not None

    answered = queue.cancel(run_id, "default", now=at)

    assert answered.status is RunStatus.RUNNING
    current = queue.get(run_id, "default")
    assert current is not None
    assert current.worker_id == "w1"
    assert current.lease_until is not None
    assert queue.is_cancelled(run_id)


def test_a_cancellation_is_logged_as_one(
    queue: PostgresRunQueue, caplog: pytest.LogCaptureFixture
) -> None:
    """`reason` said `completed` for every run that stopped on request.

    A cancelled run carries no `error_class`, so `error_class or "completed"` had
    no case for it, and deliberate stops were indistinguishable from successes in
    the one place built to make a run's history legible.
    """
    import json
    import logging

    at = datetime(2026, 3, 1, tzinfo=UTC)
    run_id = _submit(queue, at=at)

    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        queue.cancel(run_id, "default", now=at)

    reasons = [
        json.loads(record.getMessage()).get("reason")
        for record in caplog.records
        if '"run.transition"' in record.getMessage()
    ]
    assert "cancelled" in reasons, f"the cancellation was logged as {reasons}"
    assert "completed" not in reasons


# -- the second review pass, against the statements ---------------------------


def test_heartbeat_tells_a_superseded_worker_it_lost_the_run(queue: PostgresRunQueue) -> None:
    """`lease_until >= now` is not enough, because redelivery renews it.

    Once w2 claims, the row is `running` with a fresh lease, so w1's heartbeat
    matched: it was told `True`, never logged `runs.lease_lost`, and went on
    extending *w2's* lease. If w2 then died, that lease would be held open by a
    process not executing the run, and redelivery would never fire.
    """
    at = datetime(2026, 3, 1, tzinfo=UTC)
    later = at + LEASE + timedelta(seconds=1)
    run_id = _submit(queue, at=at)

    assert queue.claim(worker_id="w1", now=at, lease=LEASE, max_attempts=3) is not None
    assert queue.claim(worker_id="w2", now=later, lease=LEASE, max_attempts=3) is not None

    assert queue.heartbeat(run_id, now=later, lease=LEASE, worker_id="w1") is False
    assert queue.heartbeat(run_id, now=later, lease=LEASE, worker_id="w2") is True


def test_release_refuses_a_run_this_worker_no_longer_holds(queue: PostgresRunQueue) -> None:
    """The worst of the three missing guards, because it duplicates paid work.

    A worker that stalled past its lease, was superseded, and was then signalled
    would requeue a run w2 is actively executing — for immediate reclaim, so the
    same document is processed twice and the provider paid twice — and refund the
    attempt while doing it.
    """
    at = datetime(2026, 3, 1, tzinfo=UTC)
    later = at + LEASE + timedelta(seconds=1)
    run_id = _submit(queue, at=at)

    assert queue.claim(worker_id="w1", now=at, lease=LEASE, max_attempts=3) is not None
    live = queue.claim(worker_id="w2", now=later, lease=LEASE, max_attempts=3)
    assert live is not None

    queue.release(run_id, now=later, worker_id="w1")

    current = queue.get(run_id, "default")
    assert current is not None
    assert current.status is RunStatus.RUNNING, "a superseded worker requeued a live run"
    assert current.worker_id == "w2"
    assert current.attempts == live.attempts, "and refunded an attempt it had not spent"

    # w2, which does hold it, still can.
    queue.release(run_id, now=later, worker_id="w2")
    released = queue.get(run_id, "default")
    assert released is not None
    assert released.status is RunStatus.QUEUED
