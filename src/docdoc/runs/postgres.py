"""`RunQueue` over PostgreSQL. One table, raw SQL, no ORM.

The queue and the state store are the same table, claimed with
``FOR UPDATE SKIP LOCKED``. No broker, no coordinator, no scheduler process — a
prohibition carried over from the constitution's deferred-technology list and
enforced by an import contract rather than by review (FR-026).

**No ORM**, for the reason ADR-0010 §1 refused even SQLite for artifacts: there
is one table, the queries are a handful of statements, and the one that matters
needs ``SKIP LOCKED``, which an ORM obscures rather than helps. SQLAlchemy would
be the largest dependency in the project, arriving to manage one table
(research R6).

**Nothing here reads a clock.** Every method takes ``now``. That is FR-072, and
it is also what lets the same policy be checked against ``InMemoryRunQueue``
without a database — see ``tests/unit/test_claim_policy.py``, which asks every
lease-expiry question at an arbitrary instant and finishes in microseconds.

**No driver exception escapes.** ``psycopg`` errors become
``RunStateUnavailableError``, for the same reason the Gemini adapter maps every
provider exception: an error whose type changes when a dependency releases is
not an error a caller can handle.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from docdoc.runs.errors import (
    RunNotCancellableError,
    RunNotFoundError,
    RunStateUnavailableError,
)
from docdoc.runs.model import Run, RunOutcome, RunStatus
from docdoc.runs.observe import log_transition

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime, timedelta
    from uuid import UUID

    from docdoc.runs.queue import RunSpec

__all__ = ["PostgresRunQueue"]

#: Every column, in one place. Written out rather than ``SELECT *`` so that a
#: migration adding a column cannot silently change what ``_row_to_run`` receives.
_COLUMNS = (
    "run_id, tenant_id, blob_id, schema_identity, status, attempts, worker_id, "
    "lease_until, processing_id, failed_stage, error_class, stage_outcomes, "
    "cancel_requested, request_id, idempotency_key, created_at, updated_at, expires_at"
)

#: The same list, qualified, for the statements that ``RETURNING`` out of a join.
#: ``UPDATE … FROM prior RETURNING run_id`` is ambiguous when both relations have
#: the column, and Postgres says so rather than guessing.
_QUALIFIED = ", ".join(f"runs.{column}" for column in _COLUMNS.split(", "))


def _row_to_run(row: dict[str, Any]) -> Run:
    """One row, as the model sees it.

    ``stage_outcomes`` arrives from ``jsonb`` already decoded into a list; the
    model validates it into ``StageOutcomeRecord`` instances, so a row written by
    an older version with a different shape fails here rather than three layers
    up.
    """
    data = dict(row)
    data.pop("cancel_requested", None)  # transport state, not part of the run
    data.pop("from_state", None)  # carried for the event, not part of the run
    return Run.model_validate(data)


class PostgresRunQueue:
    """Satisfies ``docdoc.runs.queue.RunQueue``.

    Takes a connection factory rather than a DSN so a caller can supply a pooled
    connection, a transaction-scoped one for tests, or anything else that quacks
    like ``psycopg.Connection``. This module never opens or closes a pool: which
    process owns the pool is a deployment question, and a queue that opened its
    own would make every test either share global state or reach past it.
    """

    def __init__(self, connect: Callable[[], Any]) -> None:
        self._connect = connect

    # -- plumbing ----------------------------------------------------------

    def _execute(
        self,
        sql: str,
        params: tuple[Any, ...] | dict[str, Any] = (),
        *,
        fetch: str | None = None,
    ) -> Any:
        """Run one statement, mapping every driver failure to a docdoc error."""
        try:
            from psycopg.rows import dict_row
        except ImportError as exc:  # pragma: no cover - guarded by the extra
            raise RunStateUnavailableError(
                "psycopg is not installed; run state needs `pip install docdoc[postgres]`"
            ) from exc

        try:
            with self._connect() as connection, connection.cursor(row_factory=dict_row) as cursor:
                cursor.execute(sql, params)
                if fetch == "one":
                    return cursor.fetchone()
                if fetch == "all":
                    return cursor.fetchall()
                return None
        except RunStateUnavailableError:
            raise
        except Exception as exc:
            # Deliberately broad. Everything psycopg raises for an unreachable,
            # unauthenticated, or overloaded server is a case where the run store
            # is unavailable, and enumerating the driver's exception tree here
            # would make this module wrong on the driver's next release.
            raise RunStateUnavailableError(str(type(exc).__name__)) from exc

    # -- reading -----------------------------------------------------------

    def ping(self) -> None:
        """One trivial round trip. Raises when the database is unreachable.

        ``SELECT 1`` and nothing else (research R13): it touches no table, so it
        answers "can this process reach the database" without also answering
        "has the schema been applied", which is a different question with a
        different remedy and its own command (``docdoc migrate --check``).
        """
        self._execute("SELECT 1", fetch="one")

    def get(self, run_id: UUID, tenant_id: str) -> Run | None:
        """The run, or ``None`` for both "unknown" and "another tenant's".

        The tenant is a predicate in the query rather than a check after the
        fetch (FR-063). A scoped query cannot be bypassed by a caller who forgets
        the check, and there is no moment where the row exists in memory next to
        a decision about whether it should.
        """
        row = self._execute(
            f"SELECT {_COLUMNS} FROM runs WHERE run_id = %s AND tenant_id = %s",
            (run_id, tenant_id),
            fetch="one",
        )
        return None if row is None else _row_to_run(row)

    def is_cancelled(self, run_id: UUID) -> bool:
        """Whether cancellation has been requested. Read at stage boundaries."""
        row = self._execute(
            "SELECT cancel_requested FROM runs WHERE run_id = %s",
            (run_id,),
            fetch="one",
        )
        return bool(row and row["cancel_requested"])

    # -- writing -----------------------------------------------------------

    def submit(
        self,
        spec: RunSpec,
        *,
        run_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> Run:
        """Record a queued run, or return the one this key already produced.

        ``ON CONFLICT DO NOTHING`` then re-select, rather than reading first and
        inserting if absent: two API processes handling one client's retry would
        both read "not present" and both insert. The partial unique index is what
        actually enforces FR-011; this is just how the winner is reported to the
        loser (research R15).
        """
        row = self._execute(
            f"""
            INSERT INTO runs (
                run_id, tenant_id, blob_id, schema_identity, status,
                request_id, idempotency_key, created_at, updated_at, expires_at
            )
            VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s, %s, %s)
            ON CONFLICT (tenant_id, idempotency_key)
                WHERE idempotency_key IS NOT NULL DO NOTHING
            RETURNING {_COLUMNS}
            """,
            (
                run_id,
                spec.tenant_id,
                spec.blob_id,
                spec.schema_identity,
                spec.request_id,
                spec.idempotency_key,
                now,
                now,
                expires_at,
            ),
            fetch="one",
        )
        if row is not None:
            created = _row_to_run(row)
            # `from_state=None`: the run did not previously exist, which is not
            # the same as having been in a state called "absent".
            log_transition(
                run_id=created.run_id,
                tenant_id=created.tenant_id,
                from_state=None,
                to_state=str(created.status),
                attempts=created.attempts,
                reason="submitted",
            )
            return created

        # The insert was suppressed, so a run with this key already exists. **No
        # event**: nothing transitioned. An idempotent replay is a second request
        # about one run, and emitting here would make a retrying client look like
        # a queue filling up.
        existing = self._execute(
            f"SELECT {_COLUMNS} FROM runs WHERE tenant_id = %s AND idempotency_key = %s",
            (spec.tenant_id, spec.idempotency_key),
            fetch="one",
        )
        if existing is None:  # pragma: no cover - would mean the index vanished
            raise RunStateUnavailableError(
                "insert was suppressed but no run holds the idempotency key"
            )
        return _row_to_run(existing)

    def claim(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease: timedelta,
        max_attempts: int,
    ) -> Run | None:
        """Take the oldest eligible run, or ``None``.

        Two statements, in this order and for different reasons.

        **First, abandon what has run out of attempts.** A run whose lease lapsed
        for the last permitted time must not be handed to another worker, or a
        document that terminates workers takes the whole pool one process at a
        time (FR-021, SC-006). Set-based, so a backlog of them clears at once
        rather than one per claim.

        **Then claim, in a single statement.** The ``SELECT`` inside the
        ``UPDATE`` is what removes the window between choosing a candidate and
        owning it; ``SKIP LOCKED`` makes concurrent workers step over each
        other's chosen row instead of serialising on the oldest one; and the
        ``OR`` clause makes lease expiry self-healing — there is no reaper
        process to deploy, monitor, and have fail silently (research R8).

        ``now`` is a parameter rather than ``now()`` in SQL, so the whole policy
        is testable at an arbitrary instant.
        """
        abandoned = self._execute(
            """
            UPDATE runs
               SET status = 'failed',
                   error_class = 'RunAbandonedError',
                   worker_id = NULL,
                   lease_until = NULL,
                   updated_at = %(now)s
             WHERE status = 'running'
               AND lease_until < %(now)s
               AND attempts >= %(max_attempts)s
            RETURNING run_id, tenant_id, attempts, worker_id
            """,
            {"now": now, "max_attempts": max_attempts},
            fetch="all",
        )
        # One event per abandoned run, not one for the sweep. A set-based
        # statement that abandoned four runs abandoned four runs, and an operator
        # reading "a document is killing workers" needs to know which.
        for gone in abandoned or ():
            log_transition(
                run_id=gone["run_id"],
                tenant_id=gone["tenant_id"],
                from_state=str(RunStatus.RUNNING),
                to_state=str(RunStatus.FAILED),
                attempts=gone["attempts"],
                worker_id=gone["worker_id"],
                reason="RunAbandonedError",
            )

        # The candidate moves into a CTE so its **prior** status survives the
        # update. Without it the event could not say what the run transitioned
        # *from*, and a claim and a redelivery — the two things a lease exists to
        # tell apart — would be indistinguishable in the log.
        #
        # `FOR UPDATE SKIP LOCKED` is unchanged and still inside the candidate
        # selection, which is what removes the window between choosing a row and
        # owning it; moving it here changes where the text sits, not what it does.
        row = self._execute(
            f"""
            WITH candidate AS (
                SELECT run_id, status AS from_state
                  FROM runs
                 WHERE (status = 'queued'
                        OR (status = 'running' AND lease_until < %(now)s))
                   AND attempts < %(max_attempts)s
                 ORDER BY created_at
                   FOR UPDATE SKIP LOCKED
                 LIMIT 1
            )
            UPDATE runs
               SET status = 'running',
                   attempts = runs.attempts + 1,
                   worker_id = %(worker_id)s,
                   lease_until = %(now)s + %(lease)s,
                   updated_at = %(now)s
              FROM candidate
             WHERE runs.run_id = candidate.run_id
            RETURNING {_QUALIFIED}, candidate.from_state
            """,
            {
                "worker_id": worker_id,
                "now": now,
                "lease": lease,
                "max_attempts": max_attempts,
            },
            fetch="one",
        )
        if row is None:
            return None

        claimed = _row_to_run(row)
        log_transition(
            run_id=claimed.run_id,
            tenant_id=claimed.tenant_id,
            from_state=str(row["from_state"]),
            to_state=str(claimed.status),
            attempts=claimed.attempts,
            worker_id=claimed.worker_id,
            # A run that was already `running` was taken from a worker that lost
            # its lease. That is the hardest thing in this topology to debug and
            # it is the reason this event exists at all.
            reason="redelivered" if row["from_state"] == RunStatus.RUNNING else "claimed",
        )
        return claimed

    def heartbeat(self, run_id: UUID, *, now: datetime, lease: timedelta) -> bool:
        """Extend the lease. ``False`` if it was already lost.

        The ``lease_until >= now`` predicate is what makes the answer meaningful:
        a worker whose lease lapsed while it was busy has been superseded, and
        must learn that here rather than by writing a result for work another
        worker is redoing.
        """
        row = self._execute(
            """
            UPDATE runs
               SET lease_until = %(now)s + %(lease)s,
                   updated_at = %(now)s
             WHERE run_id = %(run_id)s
               AND status = 'running'
               AND lease_until >= %(now)s
            RETURNING run_id
            """,
            {"run_id": run_id, "now": now, "lease": lease},
            fetch="one",
        )
        return row is not None

    def release(self, run_id: UUID, *, now: datetime) -> None:
        """Return a claimed run to the queue immediately (FR-043).

        What a worker calls on `SIGTERM` instead of letting the lease time out,
        so a rolling restart costs no lease duration. The `worker_id` is captured
        before it is cleared, because "which worker let go" is the whole content
        of the event.
        """
        row = self._execute(
            """
            WITH prior AS (
                SELECT run_id, worker_id FROM runs WHERE run_id = %(run_id)s
            )
            UPDATE runs
               SET status = 'queued', worker_id = NULL, lease_until = NULL, updated_at = %(now)s
              FROM prior
             WHERE runs.run_id = prior.run_id AND runs.status = 'running'
            RETURNING runs.run_id, runs.tenant_id, runs.attempts,
                      prior.worker_id AS prior_worker
            """,
            {"now": now, "run_id": run_id},
            fetch="one",
        )
        if row is None:
            return
        log_transition(
            run_id=row["run_id"],
            tenant_id=row["tenant_id"],
            from_state=str(RunStatus.RUNNING),
            to_state=str(RunStatus.QUEUED),
            attempts=row["attempts"],
            worker_id=row["prior_worker"],
            reason="released",
        )

    def finish(self, run_id: UUID, outcome: RunOutcome, *, now: datetime) -> None:
        """Record a terminal state.

        ``status IN ('queued','running')`` makes this idempotent for a run
        already terminal — which matters because a worker that crashes between
        the pipeline returning and this committing will be redelivered, and the
        second attempt must not overwrite a conclusion the first one reached.
        """
        row = self._execute(
            """
            WITH prior AS (
                SELECT run_id, status, worker_id FROM runs WHERE run_id = %(run_id)s
            )
            UPDATE runs
               SET status = %(status)s,
                   processing_id = %(processing_id)s,
                   failed_stage = %(failed_stage)s,
                   error_class = %(error_class)s,
                   stage_outcomes = %(stage_outcomes)s::jsonb,
                   worker_id = NULL,
                   lease_until = NULL,
                   updated_at = %(now)s
              FROM prior
             WHERE runs.run_id = prior.run_id
               AND runs.status IN ('queued', 'running')
            RETURNING runs.run_id, runs.tenant_id, runs.attempts, runs.status AS to_state,
                      prior.status AS from_state, prior.worker_id AS prior_worker
            """,
            {
                "run_id": run_id,
                "status": str(outcome.status),
                "processing_id": outcome.processing_id,
                "failed_stage": outcome.failed_stage,
                "error_class": outcome.error_class,
                "stage_outcomes": json.dumps(
                    [record.model_dump(mode="json") for record in outcome.stage_outcomes]
                ),
                "now": now,
            },
            fetch="one",
        )
        # No row means the run was already terminal and this call was the no-op
        # its `status IN (…)` clause exists to make it. **No event**, because
        # nothing transitioned — a redelivered attempt reaching a conclusion the
        # first one already recorded must not look like a second conclusion.
        if row is None:
            return
        log_transition(
            run_id=row["run_id"],
            tenant_id=row["tenant_id"],
            from_state=str(row["from_state"]),
            to_state=str(row["to_state"]),
            attempts=row["attempts"],
            worker_id=row["prior_worker"],
            reason=outcome.error_class or "completed",
        )

    def cancel(self, run_id: UUID, tenant_id: str, *, now: datetime) -> Run:
        """Request cancellation.

        Immediate for a queued run. For a running one this records the request
        and **returns a run that still reads ``running``** — because a provider
        call already in flight completes and is billed, and reporting
        ``cancelled`` here would be the one lie this endpoint must not tell
        (FR-029).
        """
        run = self.get(run_id, tenant_id)
        if run is None:
            raise RunNotFoundError(str(run_id))
        if run.status is RunStatus.CANCELLED:
            return run  # FR-034
        if run.is_terminal:
            raise RunNotCancellableError(str(run.status))

        self._execute(
            "UPDATE runs SET cancel_requested = true, updated_at = %s WHERE run_id = %s",
            (now, run_id),
        )

        if run.status is RunStatus.QUEUED:
            # `finish` emits the queued → cancelled event, so nothing is logged
            # here: two events for one transition is the drift this whole
            # arrangement exists to avoid.
            self.finish(run_id, RunOutcome(status=RunStatus.CANCELLED), now=now)
            cancelled = self.get(run_id, tenant_id)
            if cancelled is None:  # pragma: no cover - the row cannot vanish
                raise RunStateUnavailableError("run disappeared during cancellation")
            return cancelled

        # A running run does **not** transition here — it keeps reading `running`
        # until the worker reaches a stage boundary (FR-029). So this is logged
        # as a request rather than as a change, with `from_state == to_state`,
        # which is the honest shape: an operator reading the log sees when the
        # request landed and, separately, when the run actually stopped.
        log_transition(
            run_id=run.run_id,
            tenant_id=run.tenant_id,
            from_state=str(run.status),
            to_state=str(run.status),
            attempts=run.attempts,
            worker_id=run.worker_id,
            reason="cancel_requested",
        )
        return run.model_copy(update={"updated_at": now})
