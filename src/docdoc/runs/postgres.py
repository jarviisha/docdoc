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
from docdoc.runs.identity import (
    DEFAULT_FAIRNESS_WINDOW,
    DEFAULT_STARVATION,
    new_delivery_id,
)
from docdoc.runs.model import Run, RunOutcome, RunStatus, Tombstone
from docdoc.runs.observe import log_transition, reason_for

if TYPE_CHECKING:
    from collections.abc import Callable, Collection
    from datetime import datetime, timedelta
    from uuid import UUID

    from docdoc.runs.queue import RunSpec

__all__ = ["PostgresRunQueue"]

#: Every column, in one place. Written out rather than ``SELECT *`` so that a
#: migration adding a column cannot silently change what ``_row_to_run`` receives.
_COLUMNS = (
    "run_id, tenant_id, blob_id, schema_identity, status, attempts, worker_id, "
    "lease_until, processing_id, failed_stage, error_class, stage_outcomes, "
    "cancel_requested, request_id, idempotency_key, created_at, updated_at, expires_at, "
    # Milestone 10. Named here rather than left to a `SELECT *` for the reason
    # this list exists: a migration adding a column must not silently change what
    # `_row_to_run` receives -- and adding these is exactly that migration.
    "priority, tokens_used, callback_id"
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

    # -- retention (Milestone 10, ADR-0015) --------------------------------

    def expiring(
        self,
        *,
        now: datetime,
        limit: int,
        exclude: Collection[UUID] = (),
    ) -> tuple[Run, ...]:
        """Terminal runs past their deadline, oldest first, bounded."""
        rows = self._execute(
            f"""
            SELECT {_COLUMNS} FROM runs
             WHERE status IN ('succeeded', 'failed', 'cancelled')
               AND expires_at <= %(now)s
               AND (%(exclude)s::uuid[] IS NULL OR NOT (run_id = ANY(%(exclude)s::uuid[])))
             ORDER BY expires_at
             LIMIT %(limit)s
            """,
            {"now": now, "limit": limit, "exclude": list(exclude) or None},
            fetch="all",
        )
        return tuple(_row_to_run(row) for row in rows or ())

    def retained_ids_for(
        self, tenant_id: str, *, excluding: Collection[UUID]
    ) -> tuple[frozenset[str], frozenset[str]]:
        """Both halves of the survivor set, in one read.

        `jsonb_array_elements` unrolls `stage_outcomes` so the artifact ids come
        back as rows rather than as documents this layer would have to parse. The
        blob ids come from the column beside it, in the same scan.
        """
        rows = self._execute(
            """
            SELECT runs.blob_id AS blob_id,
                   outcome ->> 'artifact_id' AS artifact_id
              FROM runs
              LEFT JOIN LATERAL jsonb_array_elements(runs.stage_outcomes) AS outcome
                     ON true
             WHERE runs.tenant_id = %(tenant_id)s
               AND NOT (runs.run_id = ANY(%(excluding)s::uuid[]))
            """,
            {"tenant_id": tenant_id, "excluding": list(excluding)},
            fetch="all",
        )
        artifacts = {row["artifact_id"] for row in rows or () if row["artifact_id"]}
        blobs = {row["blob_id"] for row in rows or () if row["blob_id"]}
        return frozenset(artifacts), frozenset(blobs)

    def entomb(self, runs: Collection[Run], *, now: datetime, policy: str) -> int:
        """Tombstone then delete, in one transaction, last of all.

        `ON CONFLICT DO NOTHING` because a sweep interrupted between the insert
        and the delete re-runs both, and a duplicate tombstone would turn a
        resumable step into a failure (FR-002).
        """
        run_ids = [run.run_id for run in runs]
        if not run_ids:
            return 0
        rows = self._execute(
            """
            WITH marked AS (
                INSERT INTO run_tombstones (run_id, tenant_id, deleted_at, policy)
                SELECT run_id, tenant_id, %(now)s, %(policy)s
                  FROM runs WHERE run_id = ANY(%(ids)s::uuid[])
                ON CONFLICT (run_id) DO NOTHING
                RETURNING run_id
            )
            DELETE FROM runs WHERE run_id = ANY(%(ids)s::uuid[]) RETURNING run_id
            """,
            {"now": now, "policy": policy, "ids": run_ids},
            fetch="all",
        )
        return len(rows or ())

    def purge_tenant_rows(self, tenant_id: str) -> dict[str, int]:
        """One statement per table, counted (FR-086)."""
        counts: dict[str, int] = {}
        for table in ("corrections", "callbacks", "deliveries", "limit_counters"):
            rows = self._execute(
                # The table name is interpolated and the tenant is a parameter.
                # `table` comes from the literal tuple above and from nowhere
                # else, which is the only form of this that is safe.
                f"DELETE FROM {table} WHERE tenant_id = %s RETURNING tenant_id",
                (tenant_id,),
                fetch="all",
            )
            counts[table] = len(rows or ())
        return counts

    def purge_rows_for(self, run_ids: Collection[UUID]) -> dict[str, int]:
        """The two tables keyed by `run_id` (FR-086)."""
        identities = list(run_ids)
        if not identities:
            return {}
        counts: dict[str, int] = {}
        for table in ("corrections", "deliveries"):
            rows = self._execute(
                # `table` comes from the literal tuple above and nowhere else,
                # which is the only form of an interpolated name that is safe.
                f"DELETE FROM {table} WHERE run_id = ANY(%s::uuid[]) RETURNING run_id",
                (identities,),
                fetch="all",
            )
            counts[table] = len(rows or ())
        return counts

    def runs_for(
        self,
        tenant_id: str,
        *,
        blob_id: str | None = None,
        limit: int,
    ) -> tuple[Run, ...]:
        """Every state, because an erasure is about a customer and not a deadline."""
        rows = self._execute(
            f"""
            SELECT {_COLUMNS} FROM runs
             WHERE tenant_id = %(tenant_id)s
               AND (%(blob_id)s::text IS NULL OR blob_id = %(blob_id)s)
             ORDER BY created_at
             LIMIT %(limit)s
            """,
            {"tenant_id": tenant_id, "blob_id": blob_id, "limit": limit},
            fetch="all",
        )
        return tuple(_row_to_run(row) for row in rows or ())

    def tombstone(self, run_id: UUID, tenant_id: str) -> Tombstone | None:
        """Scoped in the query, exactly as `get` is (FR-011)."""
        row = self._execute(
            "SELECT run_id, tenant_id, deleted_at, policy FROM run_tombstones "
            "WHERE run_id = %s AND tenant_id = %s",
            (run_id, tenant_id),
            fetch="one",
        )
        return Tombstone.model_validate(dict(row)) if row else None

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
                request_id, idempotency_key, created_at, updated_at, expires_at,
                priority, callback_id
            )
            VALUES (%s, %s, %s, %s, 'queued', %s, %s, %s, %s, %s, %s, %s)
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
                # `getattr` with the column's own default, because `RunSpec` is a
                # structural type: a caller written against Milestone 9 supplies
                # neither, and it must keep submitting runs that behave exactly
                # as they did (FR-101).
                int(getattr(spec, "priority", 0) or 0),
                getattr(spec, "callback_id", None),
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
        starvation: timedelta | None = None,
    ) -> Run | None:
        """Take the next eligible run, or ``None``.

        Two statements, in this order and for different reasons.

        **First, abandon what has run out of attempts.** A run whose lease lapsed
        for the last permitted time must not be handed to another worker, or a
        document that terminates workers takes the whole pool one process at a
        time (FR-021, SC-006). Set-based, so a backlog of them clears at once
        rather than one per claim.

        The sweep covers ``queued`` rows at the cap as well as expired ``running``
        ones, and that second case is not hypothetical. It closes a hole between
        the two predicates below: a ``queued`` run whose ``attempts`` had reached
        the cap matched *neither* the claim (which requires
        ``attempts < max_attempts``) nor a sweep scoped to ``running``. It was
        invisible to every worker and to this statement, forever — the caller
        polling ``queued`` until they gave up, and no ``RunAbandonedError`` ever
        recorded, which is the one state the attempt limit exists to produce.

        The invariant worth stating, because it is the thing that was untrue:
        **every non-terminal run is either claimable or abandonable here.**
        ``release`` no longer leaves one that is neither (see its docstring), and
        this clause means no other path can either.

        **Then claim, in a single statement.** The ``SELECT`` inside the
        ``UPDATE`` is what removes the window between choosing a candidate and
        owning it; ``SKIP LOCKED`` makes concurrent workers step over each
        other's chosen row instead of serialising on their common first choice;
        and the ``OR`` clause makes lease expiry self-healing — there is no
        reaper process to deploy, monitor, and have fail silently (research R8).

        **Milestone 10 replaced the ordering and left the guarantee.** Milestone 9
        claimed strictly oldest-first, and said in FR-024's own text that this
        described an ordering rather than a discretion because "nothing else is
        ever unequal". Three things are now unequal, and the ``ORDER BY`` applies
        them in this order:

        1. ``starved`` — past ``starvation`` a run outranks everything (FR-089),
           which is what makes a misconfigured priority ceiling a latency problem
           and never a liveness one;
        2. ``recent`` — a **deficit**: the tenant served least in the last window
           goes first, so one tenant's backlog delays another by at most one
           claim per tenant with work rather than by the size of the backlog
           (FR-090). Ranking each tenant's *queued* runs by position was the
           first design and is not round-robin — the rank recomputes over what is
           still waiting, so the backlog always has another rank-0 run and it is
           always the older one (research R10, corrected);
        3. ``priority`` — within a tenant (FR-088).

        With one tenant, no priorities, and nothing starved, every added term is
        constant and the order collapses to ``created_at``. That is Milestone 9's
        behaviour as the *default* rather than as a special case (FR-091).

        ``now`` and ``starvation`` are parameters rather than ``now()`` and a
        constant in SQL, so the whole policy is testable at an arbitrary instant.
        """
        # `None` rather than `DEFAULT_STARVATION` as the signature's default, so
        # the protocol renders the same text in code and in
        # `contracts/runs-layer.md` -- which `test_data_model_matches_the_code`
        # compares literally. A constant in a signature is a constant a document
        # has to spell as `datetime.timedelta(seconds=3600)` to match.
        bound = DEFAULT_STARVATION if starvation is None else starvation

        abandoned = self._execute(
            """
            WITH doomed AS (
                SELECT run_id, status AS from_state, worker_id
                  FROM runs
                 WHERE attempts >= %(max_attempts)s
                   AND (
                         (status = 'running' AND lease_until < %(now)s)
                      OR status = 'queued'
                       )
            )
            UPDATE runs
               SET status = 'failed',
                   error_class = 'RunAbandonedError',
                   worker_id = NULL,
                   lease_until = NULL,
                   updated_at = %(now)s
              FROM doomed
             WHERE runs.run_id = doomed.run_id
            RETURNING runs.run_id, runs.tenant_id, runs.attempts,
                      doomed.worker_id AS prior_worker, doomed.from_state
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
                from_state=str(gone["from_state"]),
                to_state=str(RunStatus.FAILED),
                attempts=gone["attempts"],
                worker_id=gone["prior_worker"],
                reason="RunAbandonedError",
            )

        # The candidate moves into a CTE so its **prior** status survives the
        # update. Without it the event could not say what the run transitioned
        # *from*, and a claim and a redelivery — the two things a lease exists to
        # tell apart — would be indistinguishable in the log.
        #
        # **The eligibility predicate sits on the locked scan, and it has to.**
        #
        # It did not, and the queue handed one run to two workers. Milestone 10
        # lifted the predicate into an `eligible` CTE and joined `candidate` to
        # it, leaving that scan with one qual: the join on `run_id`. Under READ
        # COMMITTED, `FOR UPDATE` re-evaluates a row after acquiring its lock —
        # but only against **the quals of the scan carrying the clause**. So when
        # another worker claimed a run and committed, this statement locked the
        # row, rechecked it, found the id still matched, and claimed it again.
        # `status` and `lease_until` were never re-tested, because they lived one
        # CTE away.
        #
        # `test_two_workers_racing_never_receive_the_same_run` failed 18 times in
        # 20, and a probe left a row at `attempts = 2` — two `UPDATE`s on one run,
        # two workers each holding what they believed was the lease. Sequential
        # claims were always correct, which is why every offline test missed it.
        #
        # plan.md asked this exact question and answered it wrongly: *"`FOR UPDATE
        # SKIP LOCKED` still applies to the single row the outer statement
        # selects, so two workers still cannot claim one run."* The clause does
        # still apply. What had moved is the predicate it rechecks against.
        #
        # So the `eligible` CTE is gone and its two jobs are separated by which
        # one has to be fresh:
        #
        #   * **eligibility** is on the `runs` scan below, inside the lock, where
        #     the recheck can see it. This is the correctness half.
        #   * **ordering** still reads `served`, which is a snapshot and is
        #     allowed to be: preferring the wrong tenant for one claim is a
        #     fairness wobble, and claiming a run somebody else owns is a
        #     document parsed and billed twice.
        #
        # `SKIP LOCKED` steps over a row another worker holds *uncommitted*; the
        # recheck rejects one whose claim has *committed*. Both are needed, and
        # only the first was working.
        row = self._execute(
            f"""
            WITH served AS (
                -- How much each tenant has been served lately. **This is the
                -- fairness term, and the first draft got it wrong in a way a
                -- test caught**: ranking each tenant's *queued* runs by position
                -- looks like round-robin and is not. The rank recomputes over
                -- what is still waiting, so a tenant with a backlog always has
                -- another rank-0 run, and it is always older than the newcomer's
                -- -- 201 claims in a row in the test that found it.
                --
                -- What produces alternation is a *deficit*: prefer the tenant
                -- that has had least recently. Derived from the table rather
                -- than from a counter beside it, so nothing can drift and a
                -- crash reconciles nothing.
                SELECT tenant_id, count(*) AS recent
                  FROM runs
                 WHERE updated_at > %(now)s - %(fairness_window)s::interval
                   AND status <> 'queued'
                 GROUP BY tenant_id
            ),
            candidate AS (
                SELECT runs.run_id, runs.status AS from_state
                  FROM runs
                  -- `LEFT JOIN`, and `FOR UPDATE OF runs` locks only the
                  -- preserved side. A tenant with nothing served lately has no
                  -- `served` row and must still be claimable -- it is in fact
                  -- the tenant fairness most wants to prefer.
                  LEFT JOIN served ON served.tenant_id = runs.tenant_id
                 -- **On this scan, not in a CTE above it.** This is the whole of
                 -- the fix: these three conditions are what `FOR UPDATE`
                 -- re-evaluates after it takes the lock, so a run another worker
                 -- claimed and committed no longer passes.
                 WHERE (runs.status = 'queued'
                        OR (runs.status = 'running' AND runs.lease_until < %(now)s))
                   AND runs.attempts < %(max_attempts)s
                 -- With one tenant, `recent` is the same for every candidate and
                 -- drops out; with no priorities and nothing starved, the whole
                 -- order collapses to `created_at`, which is Milestone 9's
                 -- FR-024 exactly (FR-091). The default is the old behaviour.
                 ORDER BY
                          -- Past the bound, a run outranks everything (FR-089).
                          -- A misconfigured priority ceiling is then a latency
                          -- problem and never a liveness one.
                          (runs.created_at < %(now)s - %(starvation)s::interval) DESC,
                          COALESCE(served.recent, 0),
                          runs.priority DESC,
                          runs.created_at
                   FOR UPDATE OF runs SKIP LOCKED
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
                "starvation": bound,
                "fairness_window": DEFAULT_FAIRNESS_WINDOW,
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

    def heartbeat(
        self, run_id: UUID, *, now: datetime, lease: timedelta, worker_id: str | None = None
    ) -> bool:
        """Extend the lease. ``False`` if it was already lost.

        The ``lease_until >= now`` predicate is what makes the answer meaningful:
        a worker whose lease lapsed while it was busy has been superseded, and
        must learn that here rather than by writing a result for work another
        worker is redoing.

        **``lease_until >= now`` alone was not enough**, because redelivery
        renews it. Once another worker claimed the run, the row is ``running``
        with a fresh lease, so the superseded worker's heartbeat matched — it was
        told ``True``, never logged ``runs.lease_lost``, and went on extending
        *the new owner's* lease. Two consequences, both bad in the quiet way:
        the old worker never learns to stop, and if the new owner then dies its
        lease is held open by a process that is not executing the run, so
        redelivery never fires and the run sits ``running`` until its attempts
        are swept.

        With ``worker_id``, "do I still hold this?" is the question actually
        asked. Omitting it keeps the old behaviour for callers that are not
        workers.
        """
        row = self._execute(
            f"""
            UPDATE runs
               SET lease_until = %(now)s + %(lease)s,
                   updated_at = %(now)s
             WHERE run_id = %(run_id)s
               AND status = 'running'
               AND lease_until >= %(now)s
               {"AND worker_id = %(worker_id)s" if worker_id is not None else ""}
            RETURNING run_id
            """,
            {"run_id": run_id, "now": now, "lease": lease, "worker_id": worker_id},
            fetch="one",
        )
        return row is not None

    def release(self, run_id: UUID, *, now: datetime, worker_id: str | None = None) -> None:
        """Return a claimed run to the queue immediately (FR-043).

        What a worker calls on `SIGTERM` instead of letting the lease time out,
        so a rolling restart costs no lease duration. The `worker_id` is captured
        before it is cleared, because "which worker let go" is the whole content
        of the event.

        **It gives the attempt back too**, and that is not bookkeeping tidiness.
        `attempts` bounds redelivery for one reason — a document that terminates
        the worker executing it must stop after three rather than after the whole
        pool (FR-021, SC-006) — and a graceful release is the opposite of that
        evidence: the worker is alive, it said so, and the document proved
        nothing. Counting it would make a rolling restart spend the redelivery
        budget of every run in flight, so a third restart during a backlog would
        abandon healthy runs with `RunAbandonedError`, a word that sends an
        operator to look at a document that is fine.

        It also closed a deadlock. `release` left `attempts` incremented while
        `claim` requires `attempts < max_attempts` and the sweep only touched
        `running` rows, so a run released at the cap was claimable by nobody and
        abandonable by nothing. The sweep now covers that shape as well; between
        them, a run cannot become invisible.

        **``worker_id`` guards it for the same reason ``finish`` needs one, and
        the consequence here is worse.** A worker that stalled past its lease,
        was superseded, and then received `SIGTERM` would release a run *another
        worker is executing* — requeueing it for immediate reclaim while the live
        attempt carries on, so the same document is processed twice and paid for
        twice. It would refund the attempt as well, which is exactly the wrong
        direction: the run the live worker holds would look younger than it is.
        """
        row = self._execute(
            f"""
            WITH prior AS (
                SELECT run_id, worker_id FROM runs WHERE run_id = %(run_id)s
            )
            UPDATE runs
               SET status = 'queued',
                   worker_id = NULL,
                   lease_until = NULL,
                   -- The claim that is being undone incremented this. `GREATEST`
                   -- rather than a bare subtraction so the column cannot go
                   -- negative if this is ever reached by a path that did not
                   -- claim; the check constraint would reject it and a shutdown
                   -- is the worst moment to discover that.
                   attempts = GREATEST(runs.attempts - 1, 0),
                   updated_at = %(now)s
              FROM prior
             WHERE runs.run_id = prior.run_id
               AND runs.status = 'running'
               {"AND runs.worker_id = %(worker_id)s" if worker_id is not None else ""}
            RETURNING runs.run_id, runs.tenant_id, runs.attempts,
                      prior.worker_id AS prior_worker
            """,
            {"now": now, "run_id": run_id, "worker_id": worker_id},
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

    def finish(
        self,
        run_id: UUID,
        outcome: RunOutcome,
        *,
        now: datetime,
        worker_id: str | None = None,
        only_from: RunStatus | None = None,
    ) -> bool:
        """Record a terminal state. ``True`` if this call is the one that did.

        ``status IN ('queued','running')`` makes this idempotent for a run
        already terminal — which matters because a worker that crashes between
        the pipeline returning and this committing will be redelivered, and the
        second attempt must not overwrite a conclusion the first one reached.

        **``worker_id`` is the ownership guard, and without it the status clause
        above was not enough.** `heartbeat` returning ``False`` is defined to mean
        a worker has been superseded and "must abandon what it is doing rather
        than write a result for work that is being redone" (``queue.py``), and the
        worker's lease-lost branch says the ``finish`` that follows is a no-op
        because the row is no longer claimable by this attempt. It was not a
        no-op. After redelivery the row is ``running`` again, so a stalled
        worker's late verdict matched and overwrote the live attempt's — and then
        the live attempt's own ``finish`` was the one suppressed, because by then
        the run was terminal. The result that reached the caller was the one
        computed by the worker that had already lost the run.

        Passing it makes this conditional on *still holding the lease*. Omitting
        it is for callers that are not workers — `cancel` acts on a queued run
        that no worker owns — so the guard is opt-in by the only party that has
        something to prove.

        ``only_from`` narrows the accepted prior state. `cancel` uses it to make
        its queued-run path atomic; see there.

        **The delivery is enqueued here, in this statement** (T112, FR-053). A
        run carrying a `callback_id` reaches a terminal state and gains a pending
        delivery in one transaction, so there is no window in which a run is
        finished and its notification is lost — which is what a second statement
        after the commit would have. It is a data-modifying CTE rather than a
        trigger for the reason this module has no ORM: the statement that does it
        should be the statement you can read.

        ``ON CONFLICT (run_id) DO NOTHING`` is the constraint doing the work
        (FR-058). A redelivered attempt whose `finish` is suppressed inserts
        nothing anyway, because the UPDATE returned no row; the clause covers the
        remaining case, which is two workers concluding one run at once.
        """
        row = self._execute(
            f"""
            WITH prior AS (
                SELECT run_id, status, worker_id FROM runs WHERE run_id = %(run_id)s
            ),
            updated AS (
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
                   AND {
                "runs.status = %(only_from)s"
                if only_from
                else "runs.status IN ('queued', 'running')"
            }
                   {"AND runs.worker_id = %(worker_id)s" if worker_id is not None else ""}
                RETURNING runs.run_id, runs.tenant_id, runs.attempts,
                          runs.status AS to_state, runs.callback_id,
                          prior.status AS from_state, prior.worker_id AS prior_worker
            ),
            notified AS (
                INSERT INTO deliveries (delivery_id, tenant_id, run_id, callback_id,
                                        state, attempts, next_attempt_at,
                                        created_at, updated_at)
                SELECT %(delivery_id)s, tenant_id, run_id, callback_id,
                       'pending', 0, %(now)s, %(now)s, %(now)s
                  FROM updated
                 WHERE callback_id IS NOT NULL
                ON CONFLICT (run_id) DO NOTHING
            )
            SELECT * FROM updated
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
                "worker_id": worker_id,
                "only_from": None if only_from is None else str(only_from),
                # Allocated whether or not it is used, because a parameter that
                # exists conditionally would mean two statements. Unused when the
                # run carries no callback, which is the ordinary case.
                "delivery_id": new_delivery_id(),
            },
            fetch="one",
        )
        # No row means this call changed nothing: the run was already terminal,
        # or this worker no longer holds it. **No event**, because nothing
        # transitioned — a redelivered attempt reaching a conclusion the first one
        # already recorded must not look like a second conclusion.
        if row is None:
            return False
        log_transition(
            run_id=row["run_id"],
            tenant_id=row["tenant_id"],
            from_state=str(row["from_state"]),
            to_state=str(row["to_state"]),
            attempts=row["attempts"],
            worker_id=row["prior_worker"],
            reason=reason_for(outcome),
        )
        return True

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
            # `only_from` is what makes this safe, and the unguarded version was a
            # time-of-check-to-time-of-use bug with a real cost. The status came
            # from the `get` above; between that read and this write a worker can
            # claim the run, and `finish` accepts a `running` row. So the run went
            # to `cancelled` — worker_id cleared, lease cleared, stage outcomes
            # blanked — while the worker kept executing it and kept paying for
            # provider calls. That is exactly what FR-029 says must not happen for
            # a running run, arriving through the path for queued ones.
            #
            # Conditioning on `status = 'queued'` moves the decision into the
            # statement. The claim either got there first or it did not, and the
            # database says which.
            #
            # `finish` emits the queued → cancelled event, so nothing is logged
            # here: two events for one transition is the drift this whole
            # arrangement exists to avoid.
            if self.finish(
                run_id,
                RunOutcome(status=RunStatus.CANCELLED),
                now=now,
                only_from=RunStatus.QUEUED,
            ):
                cancelled = self.get(run_id, tenant_id)
                if cancelled is None:  # pragma: no cover - the row cannot vanish
                    raise RunStateUnavailableError("run disappeared during cancellation")
                return cancelled

            # Lost the race: it was claimed in the moment between the read and
            # the write. `cancel_requested` is already set, so this is now
            # precisely the running case below — the worker will observe it at
            # the next stage boundary. Re-read rather than reuse `run`, which
            # describes a state that is no longer true.
            claimed = self.get(run_id, tenant_id)
            if claimed is None:  # pragma: no cover - the row cannot vanish
                raise RunStateUnavailableError("run disappeared during cancellation")
            if claimed.is_terminal:
                # A concurrent cancel finished it. Idempotent (FR-034), and no
                # event: this call transitioned nothing.
                return claimed
            run = claimed

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
