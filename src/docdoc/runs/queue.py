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
    from collections.abc import Collection
    from datetime import datetime, timedelta
    from uuid import UUID

    from docdoc.runs.model import Run, RunKind, RunOutcome, RunStatus, Tombstone

__all__ = ["RunQueue", "RunSpec"]


class RunSpec(Protocol):
    """What a submission carries before it is a run.

    A structural type rather than a model: the API builds one from a request and
    the CLI could build one from arguments, and neither should have to import a
    constructor to describe four strings.
    """

    tenant_id: str
    blob_id: str
    schema_identity: str | None
    request_id: str | None
    idempotency_key: str | None

    #: Milestone 10, and both are read with a default rather than required. A
    #: structural type has no way to be optional, so `submit` reads them with
    #: `getattr` and falls back to what the column already means — which is what
    #: keeps a Milestone 9 caller building a spec of four strings and getting the
    #: run it always got (FR-101).
    #:
    #: `priority` is granted rather than requested: the route clamps it to the
    #: tenant's ceiling before it reaches here, so nothing below this line can be
    #: used to escalate past another tenant's queue (FR-087b).
    priority: int
    callback_id: UUID | None
    #: 014, read with `getattr` on the same terms: a spec that names no kind is
    #: an extraction run, and only an OCR spec carries a parser and a language.
    kind: str
    ocr_parser: str | None
    ocr_language: str | None


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
        starvation: timedelta | None = None,
        kind: RunKind | None = None,
    ) -> Run | None:
        """Take the oldest eligible run, or `None`.

        `kind`, when given, claims only runs of that kind -- how an OCR worker
        and an extraction worker share one table without either delaying the
        other (ADR-0022). `None` claims any kind, which is every worker before
        014. The abandonment half stays kind-agnostic: a run out of attempts is
        finished whichever worker notices.

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

    def count_active(self, *, kind: RunKind, tenant_id: str | None = None) -> int:
        """Runs of `kind` that are queued or running; one tenant's, or everyone's.

        What the OCR caps count on every submission (ADR-0022). Counted live,
        never stored, for the reason `concurrent_runs` is: a counter beside the
        table is a second source of truth that a crash leaves wrong.
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

        **An OCR run's deadline moves to count from here** (ADR-0022): the
        period it was submitted with (`expires_at - created_at`) is re-applied
        from `now`, so a deep queue cannot sweep a result before anybody could
        fetch it. An extraction run's deadline is untouched.

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

    # -- retention (Milestone 10, ADR-0015) -----------------------------------
    #
    # Three methods and not one, because the sweep computes a **difference** and
    # a difference needs both sides. They are on the protocol rather than in
    # `postgres.py` alone for the reason `claim` is: the policy is what is worth
    # testing, and a fake that cannot be swept is a fake retention cannot be
    # tested against.

    def expiring(
        self,
        *,
        now: datetime,
        limit: int,
        exclude: Collection[UUID] = (),
        kind: RunKind | None = None,
    ) -> tuple[Run, ...]:
        """Terminal runs past their retention deadline, oldest first.

        `kind`, when given, returns only runs of that kind: the OCR sweep runs
        when run retention is off and must not reach an extraction run
        (ADR-0022).

        Never returns a run that is queued, running, or holding an unexpired
        lease (FR-003): a retention policy that can delete work in flight is a
        policy that loses paid work.

        `exclude` carries the runs a live correction pins (FR-013). Passed in
        rather than joined here, because which runs are pinned is the corrections
        store's question and this layer does not own that table.

        `limit` is what makes a year of accumulated rows progress across many
        ticks instead of one unbounded transaction (FR-015).
        """
        ...

    def retained_ids_for(
        self, tenant_id: str, *, excluding: Collection[UUID]
    ) -> tuple[frozenset[str], frozenset[str]]:
        """`(artifact_ids, blob_ids)` the tenant's **surviving** runs still name.

        The `survivors` half of ADR-0015 §1, and **both halves in one read**. An
        earlier draft returned artifacts only and deleted a removed run's blob
        outright — which is wrong whenever two runs of a tenant were submitted
        against the same document, the single most ordinary thing a tenant does.
        A blob survives on exactly the terms an artifact does.

        Artifact ids come from `stage_outcomes`, which already records one per
        stage; blob ids come from the `blob_id` column. Nothing is re-derived,
        and re-deriving would need a parser version a reconfigured deployment no
        longer has.

        `excluding` is the batch being removed, so a caller does not have to
        subtract it afterwards and cannot forget to.
        """
        ...

    def entomb(self, runs: Collection[Run], *, now: datetime, policy: str) -> int:
        """Write a tombstone per run and remove the rows. Returns the count.

        **Last**, after the content is gone (ADR-0015 §3). A run row holds the
        `stage_outcomes` naming what still needs deleting, so removing it while
        the content survives loses the only record of the work — and the next
        sweep would have nothing to resume from.
        """
        ...

    def purge_tenant_rows(self, tenant_id: str) -> dict[str, int]:
        """Remove every row this milestone's tables hold for a tenant.

        Corrections, callbacks, deliveries, and limit counters (FR-086). Returns
        counts by table.

        **`corrections` is why this exists.** It is the one table Milestone 10
        adds that can hold a value taken from a document — a reviewer states the
        predicted value and the corrected one — so an erasure that removed blobs,
        artifacts, and run rows and left it behind would answer "erased" about
        data that is still there.

        The other three carry no document content and go with it anyway: a
        callback's URL, a delivery's history, and a counter are all facts about a
        customer who has asked to be gone.

        Tombstones are **not** removed. They carry an identity, a tenant, a time,
        and a policy, and they are what lets the owner be told their run was here
        and is not (FR-011).
        """
        ...

    def purge_rows_for(self, run_ids: Collection[UUID]) -> dict[str, int]:
        """Remove the corrections and deliveries these runs own (FR-086).

        The narrower half of `purge_tenant_rows`, for erasing **one document**:
        the tenant is not being erased, so its callbacks and its counters stay,
        and what goes is what belonged to the runs over that document.
        """
        ...

    def runs_for(
        self,
        tenant_id: str,
        *,
        blob_id: str | None = None,
        limit: int,
    ) -> tuple[Run, ...]:
        """A tenant's runs, or only those over one document. Bounded.

        What erasure works from (FR-005). Unlike `expiring` this returns runs in
        **every** state, because an erasure request is about a customer's data
        and a run that is still queued is still theirs — FR-010 requires those to
        be cancelled rather than skipped.
        """
        ...

    def page_runs(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        before: tuple[datetime, UUID] | None = None,
        limit: int,
    ) -> tuple[Run, ...]:
        """One page of a tenant's runs, newest first (Milestone 11 FR-013).

        Keyset and never `OFFSET`: an offset page shifts under concurrent
        submission, which would make FR-018's "every run exactly once" false in
        production and true in every test.

        `before` is the `(created_at, run_id)` of the last row on the previous
        page. Separate from `runs_for`, which exists for erasure and wants every
        state in ascending order with no cursor — one function with two callers
        that want opposite things is how both come to be served badly.
        """
        ...

    def page_all_runs(
        self,
        *,
        tenant_id: str | None,
        status: str | None,
        before: tuple[datetime, UUID] | None,
        limit: int,
        kind: str | None = None,
    ) -> tuple[Run, ...]:
        """`page_runs` across tenants: the predicate is dropped when `tenant_id` is `None`.

        The console's read (ADR-0023 §3). Only a signed-in operator reaches it;
        a tenant's own listing stays `page_runs`, where the predicate is not
        optional. `kind`, when given, is one more predicate.
        """
        ...

    def status_counts(self, *, since: datetime) -> tuple[tuple[str, str, int], ...]:
        """`(kind, status, count)` across tenants, non-zero rows only.

        `queued` and `running` count at any age; the three terminal statuses
        count only when `updated_at >= since`. The console Overview's tiles.
        """
        ...

    def latest(self, *, status: str, limit: int) -> tuple[Run, ...]:
        """Up to `limit` runs in `status`, across tenants, newest `updated_at` first."""
        ...

    def owner_of(self, run_id: UUID) -> str | None:
        """The tenant a run, or its tombstone, belongs to; `None` if neither exists.

        How the console resolves a run it was handed by identity alone, so that
        every read after it goes through the tenant-scoped `get` and
        `tombstone` exactly as a tenant's own would.
        """
        ...

    def tombstone(self, run_id: UUID, tenant_id: str) -> Tombstone | None:
        """What remains of a removed run, for the tenant that owned it.

        Scoped in the query rather than checked after the fetch, exactly as `get`
        is: another tenant asking gets `None`, which the route renders
        identically to an identifier that never existed (FR-011).
        """
        ...

    def ping(self) -> None:
        """Reach the store and return, or raise.

        What readiness asks (FR-054). On the protocol rather than only on the
        Postgres implementation because both process types probe through this
        surface, and because a fake that cannot be made unreachable is a fake
        readiness cannot be tested against.
        """
        ...
