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


def _row_to_run(row: dict[str, Any]) -> Run:
    """One row, as the model sees it.

    ``stage_outcomes`` arrives from ``jsonb`` already decoded into a list; the
    model validates it into ``StageOutcomeRecord`` instances, so a row written by
    an older version with a different shape fails here rather than three layers
    up.
    """
    data = dict(row)
    data.pop("cancel_requested", None)  # transport state, not part of the run
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
            return _row_to_run(row)

        # The insert was suppressed, so a run with this key already exists.
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
        self._execute(
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
            """,
            {"now": now, "max_attempts": max_attempts},
        )

        row = self._execute(
            f"""
            UPDATE runs
               SET status = 'running',
                   attempts = attempts + 1,
                   worker_id = %(worker_id)s,
                   lease_until = %(now)s + %(lease)s,
                   updated_at = %(now)s
             WHERE run_id = (
                     SELECT run_id
                       FROM runs
                      WHERE (status = 'queued'
                             OR (status = 'running' AND lease_until < %(now)s))
                        AND attempts < %(max_attempts)s
                      ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                      LIMIT 1
                   )
            RETURNING {_COLUMNS}
            """,
            {
                "worker_id": worker_id,
                "now": now,
                "lease": lease,
                "max_attempts": max_attempts,
            },
            fetch="one",
        )
        return None if row is None else _row_to_run(row)

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
        """Return a claimed run to the queue immediately (FR-043)."""
        self._execute(
            """
            UPDATE runs
               SET status = 'queued', worker_id = NULL, lease_until = NULL, updated_at = %s
             WHERE run_id = %s AND status = 'running'
            """,
            (now, run_id),
        )

    def finish(self, run_id: UUID, outcome: RunOutcome, *, now: datetime) -> None:
        """Record a terminal state.

        ``status IN ('queued','running')`` makes this idempotent for a run
        already terminal — which matters because a worker that crashes between
        the pipeline returning and this committing will be redelivered, and the
        second attempt must not overwrite a conclusion the first one reached.
        """
        self._execute(
            """
            UPDATE runs
               SET status = %(status)s,
                   processing_id = %(processing_id)s,
                   failed_stage = %(failed_stage)s,
                   error_class = %(error_class)s,
                   stage_outcomes = %(stage_outcomes)s::jsonb,
                   worker_id = NULL,
                   lease_until = NULL,
                   updated_at = %(now)s
             WHERE run_id = %(run_id)s
               AND status IN ('queued', 'running')
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
            self.finish(run_id, RunOutcome(status=RunStatus.CANCELLED), now=now)
            cancelled = self.get(run_id, tenant_id)
            if cancelled is None:  # pragma: no cover - the row cannot vanish
                raise RunStateUnavailableError("run disappeared during cancellation")
            return cancelled

        return run.model_copy(update={"updated_at": now})
