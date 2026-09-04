"""An in-memory `RunQueue`, so the claim policy is testable with no database.

This is the reason `RunQueue` is a protocol at all (Principle XI wants a
present-tense justification for every abstraction). The policy questions —
oldest-first, lease expiry, the attempt limit, cancellation refusal — are
questions about *rules*, not about SQL, and a test that needs a container to ask
them is a test most contributors will not run.

It is deliberately a **faithful** fake rather than a convenient one: it enforces
the same invariants the check constraint does, refuses the same transitions, and
returns `False` from `heartbeat` under the same condition. A fake that is easier
to satisfy than the real thing tests nothing about the real thing.

Not thread-safe, and it does not need to be: a worker executes one run at a time
(FR-025) and these tests drive it from one thread.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docdoc.runs.errors import (
    RunNotCancellableError,
    RunNotFoundError,
    RunStateUnavailableError,
)
from docdoc.runs.model import Run, RunOutcome, RunStatus

# `reason_for` is imported rather than reimplemented, so the fake cannot
# describe a transition differently from the real queue -- which is the whole
# contract of this file.
from docdoc.runs.observe import log_transition, reason_for

if TYPE_CHECKING:
    from datetime import datetime, timedelta
    from uuid import UUID

    from docdoc.runs.queue import RunSpec

__all__ = ["InMemoryRunQueue"]


class InMemoryRunQueue:
    """Satisfies `docdoc.runs.queue.RunQueue`."""

    def __init__(self) -> None:
        self._runs: dict[UUID, Run] = {}
        self._cancel_requested: set[UUID] = set()
        #: Set by a test to make every call fail the way an unreachable database
        #: does. Faithfulness again: readiness cannot be tested against a fake
        #: that is always up.
        self.unreachable = False

    # -- reading -----------------------------------------------------------

    def ping(self) -> None:
        self._check_reachable()

    def _check_reachable(self) -> None:
        if self.unreachable:
            raise RunStateUnavailableError("the fake queue was told it is unreachable")

    def get(self, run_id: UUID, tenant_id: str) -> Run | None:
        run = self._runs.get(run_id)
        if run is None or run.tenant_id != tenant_id:
            # One return for both cases, which is FR-066. Writing it as two
            # branches with the same body would invite one of them to grow a
            # different message later.
            return None
        return run

    def is_cancelled(self, run_id: UUID) -> bool:
        return run_id in self._cancel_requested

    # -- writing -----------------------------------------------------------

    def submit(
        self,
        spec: RunSpec,
        *,
        run_id: UUID,
        now: datetime,
        expires_at: datetime,
    ) -> Run:
        self._check_reachable()
        if spec.idempotency_key is not None:
            for existing in self._runs.values():
                if (
                    existing.tenant_id == spec.tenant_id
                    and existing.idempotency_key == spec.idempotency_key
                ):
                    return existing

        run = Run(
            run_id=run_id,
            tenant_id=spec.tenant_id,
            blob_id=spec.blob_id,
            schema_identity=spec.schema_identity,
            status=RunStatus.QUEUED,
            request_id=spec.request_id,
            idempotency_key=spec.idempotency_key,
            created_at=now,
            updated_at=now,
            expires_at=expires_at,
        )
        self._runs[run_id] = run
        log_transition(
            run_id=run.run_id,
            tenant_id=run.tenant_id,
            from_state=None,
            to_state=str(run.status),
            attempts=run.attempts,
            reason="submitted",
        )
        return run

    def claim(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease: timedelta,
        max_attempts: int,
    ) -> Run | None:
        # **First, abandon what has run out of attempts** — every one of them,
        # not just the oldest. `PostgresRunQueue.claim` does this as a separate
        # set-based UPDATE before the claim query, so a backlog clears at once
        # and, crucially, an abandoned run does not consume the claim.
        #
        # This fake used to abandon the oldest and then `return None`, which made
        # a poison document *block the runs behind it* for one round each — a
        # behaviour the real queue does not have. That is the failure mode this
        # file's docstring warns about, arriving from the unexpected direction:
        # not a fake that is easier to satisfy, but one that is differently
        # wrong, and therefore describes a system nobody deployed.
        #
        # `queued` rows at the cap are swept too, matching the real statement.
        # That case exists because `release` used to leave one — claimable by
        # nobody, since the claim below requires `attempts < max_attempts`, and
        # abandonable by nothing, since this loop only looked at expired leases.
        # `release` no longer creates the shape and this no longer ignores it.
        for run in list(self._runs.values()):
            if run.attempts >= max_attempts and (
                run.lease_expired_at(now) or run.status is RunStatus.QUEUED
            ):
                # `finish` emits the running -> failed event, exactly as the
                # Postgres sweep does. One event per abandoned run.
                self.finish(
                    run.run_id,
                    RunOutcome(status=RunStatus.FAILED, error_class="RunAbandonedError"),
                    now=now,
                )

        eligible = [
            run
            for run in self._runs.values()
            if (run.status is RunStatus.QUEUED or run.lease_expired_at(now))
            and run.attempts < max_attempts
        ]
        if not eligible:
            return None

        # FR-024, and the reason it is `min` rather than a scan with a
        # tie-break: with no priority classes nothing is ever unequal, so
        # creation order is the whole ordering.
        run = min(eligible, key=lambda r: r.created_at)

        claimed = run.model_copy(
            update={
                "status": RunStatus.RUNNING,
                "attempts": run.attempts + 1,
                "worker_id": worker_id,
                "lease_until": now + lease,
                "updated_at": now,
            }
        )
        self._runs[run.run_id] = claimed
        log_transition(
            run_id=claimed.run_id,
            tenant_id=claimed.tenant_id,
            from_state=str(run.status),
            to_state=str(claimed.status),
            attempts=claimed.attempts,
            worker_id=claimed.worker_id,
            # A run that was already `running` was taken from a worker that lost
            # its lease, which is what a lease exists to make visible.
            reason="redelivered" if run.status is RunStatus.RUNNING else "claimed",
        )
        return claimed

    def heartbeat(
        self, run_id: UUID, *, now: datetime, lease: timedelta, worker_id: str | None = None
    ) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status is not RunStatus.RUNNING or run.lease_expired_at(now):
            return False
        # Redelivery renews the lease, so the two checks above pass for a worker
        # that has already been superseded. Only this one tells it so.
        if worker_id is not None and run.worker_id != worker_id:
            return False
        self._runs[run_id] = run.model_copy(update={"lease_until": now + lease, "updated_at": now})
        return True

    def release(self, run_id: UUID, *, now: datetime, worker_id: str | None = None) -> None:
        run = self._runs.get(run_id)
        if run is None or run.is_terminal:
            return
        # `status = 'running'` is a predicate in the real statement, and the fake
        # accepted a queued run — so a double release looked fine here and did
        # nothing there, which is the direction that hides a bug rather than
        # inventing one.
        if run.status is not RunStatus.RUNNING:
            return
        if worker_id is not None and run.worker_id != worker_id:
            return
        self._runs[run_id] = run.model_copy(
            update={
                "status": RunStatus.QUEUED,
                "worker_id": None,
                "lease_until": None,
                # The claim being undone incremented this, and a graceful release
                # is not the evidence `max_attempts` bounds. See
                # `PostgresRunQueue.release` for why this is correctness rather
                # than tidiness.
                "attempts": max(run.attempts - 1, 0),
                "updated_at": now,
            }
        )
        log_transition(
            run_id=run.run_id,
            tenant_id=run.tenant_id,
            from_state=str(run.status),
            to_state=str(RunStatus.QUEUED),
            attempts=run.attempts - 1,
            worker_id=run.worker_id,
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
        run = self._runs.get(run_id)
        # Already terminal: the no-op its Postgres counterpart's
        # `status IN (…)` clause makes, and no event, because nothing changed.
        if run is None or run.is_terminal:
            return False
        # The same two guards the real statement carries, and for the same
        # reasons. This fake used to have neither, so the ownership hole was
        # invisible to every test that used it — a fake that is *differently*
        # wrong describes a system nobody deployed, which is the failure this
        # file's docstring warns about.
        if only_from is not None and run.status is not only_from:
            return False
        if worker_id is not None and run.worker_id != worker_id:
            return False
        self._runs[run_id] = run.model_copy(
            update={
                "status": outcome.status,
                "processing_id": outcome.processing_id,
                "failed_stage": outcome.failed_stage,
                "error_class": outcome.error_class,
                "stage_outcomes": outcome.stage_outcomes,
                "worker_id": None,
                "lease_until": None,
                "updated_at": now,
            }
        )
        log_transition(
            run_id=run.run_id,
            tenant_id=run.tenant_id,
            from_state=str(run.status),
            to_state=str(outcome.status),
            attempts=run.attempts,
            worker_id=run.worker_id,
            reason=reason_for(outcome),
        )
        return True

    def cancel(self, run_id: UUID, tenant_id: str, *, now: datetime) -> Run:
        run = self.get(run_id, tenant_id)
        if run is None:
            # `RunNotFoundError`, not `KeyError`: the fake raised the latter
            # until the cancel route needed one answer for "unknown" and
            # "another tenant's", and a fake that fails differently from the
            # real thing is a fake the route cannot be tested against.
            raise RunNotFoundError(str(run_id))

        if run.status is RunStatus.CANCELLED:
            return run  # FR-034
        if run.is_terminal:
            raise RunNotCancellableError(str(run.status))

        self._cancel_requested.add(run_id)
        if run.status is RunStatus.QUEUED:
            # `only_from` exactly as the real `cancel` passes it. Without it the
            # claim-race branch below is unreachable through this fake, so the
            # guard that closes the race could be deleted and every test here
            # would still pass.
            if self.finish(
                run_id,
                RunOutcome(status=RunStatus.CANCELLED),
                now=now,
                only_from=RunStatus.QUEUED,
            ):
                return self._runs[run_id]
            run = self._runs[run_id]
            if run.is_terminal:
                return run

        # Running: the request is recorded and the run keeps reading `running`
        # until the worker reaches a stage boundary. Returning `cancelled` here
        # would be the one lie this endpoint must not tell (FR-029).
        #
        # Logged as a request rather than as a change, with `from_state ==
        # to_state`, which is the honest shape: nothing transitioned yet.
        log_transition(
            run_id=run.run_id,
            tenant_id=run.tenant_id,
            from_state=str(run.status),
            to_state=str(run.status),
            attempts=run.attempts,
            worker_id=run.worker_id,
            reason="cancel_requested",
        )
        return run
