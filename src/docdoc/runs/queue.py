"""The run store, as a protocol.

Every method that needs an instant or an identity **takes it as a parameter**.
None of them reads a clock or generates a uuid. That is FR-072 expressed as a
signature rather than as a rule somebody has to remember, and it buys two things.

**The policy becomes testable without a database.** `claim` is a pure function of
`(rows, now, lease)`, so lease expiry, oldest-first ordering, and the attempt
limit are all checkable at arbitrary instants against an in-memory
implementation, with no container and no sleeping. `tests/fixtures/run_queue.py`
is that implementation, and it is the present-tense justification Principle XI
asks of any protocol with one production implementation — the other being that
the API and the worker both depend on this surface while only the worker needs
the loop around it.

**The determinism guard stays green.** A clock read inside `postgres.py` would
pass CI today; a clock read that drifted one layer lower would not, and this
shape means there is never a reason to move one.

**Tenant scoping is in the query, not after the fetch.** `get` and `cancel` take
a `tenant_id` and are defined to be indistinguishable for "no such run" and "not
yours" (FR-066). A caller cannot forget a check that does not exist.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from datetime import datetime, timedelta
    from uuid import UUID

    from docdoc.runs.model import Run, RunOutcome, RunStatus

__all__ = ["RunQueue", "RunSpec"]


class RunSpec(Protocol):
    """What a submission carries before it is a run.

    A structural type rather than a model: the API builds one from a request and
    the CLI could build one from arguments, and neither should have to import a
    constructor to describe four strings.
    """

    tenant_id: str
    blob_id: str
    schema_identity: str
    request_id: str | None
    idempotency_key: str | None


class RunQueue(Protocol):
    """Where runs are recorded, claimed, and finished."""

    def submit(
        self,
        spec: RunSpec,
        *,
        run_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> Run:
        """Record a queued run and return it.

        Idempotent per `(tenant_id, idempotency_key)` when the spec carries one:
        a repeat returns the original run rather than creating a second, and the
        database enforces it. Two API processes handling a client's retry
        concurrently would both read "not present" and both insert (R15).
        """
        ...

    def get(self, run_id: UUID, tenant_id: str) -> Run | None:
        """The run, or `None` for both "unknown" and "another tenant's"."""
        ...

    def claim(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease: timedelta,
        max_attempts: int,
    ) -> Run | None:
        """Take the oldest eligible run, or `None`.

        Eligible means queued, or running with a lapsed lease — expiry is a
        clause in the claim rather than a reaper process to deploy and have fail
        silently (R8). Ordering is by `created_at` ascending, which is FR-024.

        Increments `attempts` in the same statement that takes the run, so no
        window exists between selecting a candidate and owning it.

        `max_attempts` bounds redelivery: a run whose lease lapsed for the last
        permitted time is finished as abandoned rather than handed to another
        worker, so a document that terminates workers stops after that many
        rather than after all of them.
        """
        ...

    def heartbeat(
        self, run_id: UUID, *, now: datetime, lease: timedelta, worker_id: str | None = None
    ) -> bool:
        """Extend the lease. `False` if it was already lost.

        A worker that reads `False` has been superseded — another worker claimed
        the run after its lease lapsed — and must abandon what it is doing rather
        than write a result for work that is being redone.

        `worker_id`, when given, makes that answer true. Without it a superseded
        worker is told `True` and keeps extending *the new owner's* lease, so it
        never learns to stop and the run cannot be redelivered if the new owner
        dies too.
        """
        ...

    def release(self, run_id: UUID, *, now: datetime, worker_id: str | None = None) -> None:
        """Return a claimed run to the queue immediately.

        What a worker calls on `SIGTERM` instead of letting the lease time out,
        so a rolling restart costs no lease duration (FR-043).

        **Gives the attempt back.** The claim being undone consumed one, and a
        graceful release is not the evidence `max_attempts` bounds — the worker
        is alive and the document proved nothing. See `PostgresRunQueue.release`.

        `worker_id`, when given, requires the run to still be held by that
        worker. A superseded worker shutting down would otherwise requeue a run
        another worker is executing — and refund its attempt.
        """
        ...

    def finish(
        self,
        run_id: UUID,
        outcome: RunOutcome,
        *,
        now: datetime,
        worker_id: str | None = None,
        only_from: RunStatus | None = None,
    ) -> bool:
        """Record a terminal state. `True` if this call is the one that did.

        Idempotent for a run already terminal, which is what the `False` return
        reports rather than leaving a caller to infer it.

        `worker_id`, when given, requires the run to **still be held by that
        worker**. A worker that lost its lease mid-run must not write a verdict
        for work another worker is redoing — `heartbeat` returning `False` says
        so, and this is what makes the saying true. Callers that are not workers
        omit it.

        `only_from`, when given, requires that prior state exactly. `cancel` uses
        it so that stopping a queued run cannot stop one that was claimed a
        microsecond earlier.
        """
        ...

    def cancel(self, run_id: UUID, tenant_id: str, *, now: datetime) -> Run:
        """Request cancellation.

        Immediate for a queued run: it moves to cancelled and is never claimed.
        For a running one this only *records the request* — the worker observes
        it at the next stage boundary, and a provider call already in flight
        completes and is billed (FR-029). The returned run still reads
        `running`, and the contract says so rather than letting a caller infer
        that the cancel failed.

        Raises `RunNotCancellableError` for a terminal run, naming the state
        (FR-031), and is idempotent for one already cancelled (FR-034).
        """
        ...

    def is_cancelled(self, run_id: UUID) -> bool:
        """Whether cancellation has been requested. Read at stage boundaries."""
        ...

    def ping(self) -> None:
        """Reach the store and return, or raise.

        What readiness asks (FR-054). On the protocol rather than only on the
        Postgres implementation because both process types probe through this
        surface, and because a fake that cannot be made unreachable is a fake
        readiness cannot be tested against.
        """
        ...
