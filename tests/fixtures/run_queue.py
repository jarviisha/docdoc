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

from docdoc.runs.errors import RunNotCancellableError
from docdoc.runs.model import Run, RunOutcome, RunStatus

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

    # -- reading -----------------------------------------------------------

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
        return run

    def claim(
        self,
        *,
        worker_id: str,
        now: datetime,
        lease: timedelta,
        max_attempts: int,
    ) -> Run | None:
        eligible = [
            run
            for run in self._runs.values()
            if run.status is RunStatus.QUEUED or run.lease_expired_at(now)
        ]
        if not eligible:
            return None

        # FR-024, and the reason it is `min` rather than a scan with a
        # tie-break: with no priority classes nothing is ever unequal, so
        # creation order is the whole ordering.
        run = min(eligible, key=lambda r: r.created_at)

        if run.attempts >= max_attempts:
            # The lease lapsed for the last permitted time. Finishing it here
            # rather than handing it out again is what bounds the poison
            # document to `max_attempts` workers instead of all of them.
            self.finish(
                run.run_id,
                RunOutcome(status=RunStatus.FAILED, error_class="RunAbandonedError"),
                now=now,
            )
            return None

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
        return claimed

    def heartbeat(self, run_id: UUID, *, now: datetime, lease: timedelta) -> bool:
        run = self._runs.get(run_id)
        if run is None or run.status is not RunStatus.RUNNING or run.lease_expired_at(now):
            return False
        self._runs[run_id] = run.model_copy(update={"lease_until": now + lease, "updated_at": now})
        return True

    def release(self, run_id: UUID, *, now: datetime) -> None:
        run = self._runs.get(run_id)
        if run is None or run.is_terminal:
            return
        self._runs[run_id] = run.model_copy(
            update={
                "status": RunStatus.QUEUED,
                "worker_id": None,
                "lease_until": None,
                "updated_at": now,
            }
        )

    def finish(self, run_id: UUID, outcome: RunOutcome, *, now: datetime) -> None:
        run = self._runs.get(run_id)
        if run is None or run.is_terminal:
            return
        self._runs[run_id] = run.model_copy(
            update={
                "status": outcome.status,
                "processing_id": outcome.processing_id,
                "failed_stage": outcome.failed_stage,
                "error_class": outcome.error_class,
                "stage_outcomes": outcome.stage_outcomes,
                "lease_until": None,
                "updated_at": now,
            }
        )

    def cancel(self, run_id: UUID, tenant_id: str, *, now: datetime) -> Run:
        run = self.get(run_id, tenant_id)
        if run is None:
            raise KeyError(run_id)

        if run.status is RunStatus.CANCELLED:
            return run  # FR-034
        if run.is_terminal:
            raise RunNotCancellableError(str(run.status))

        self._cancel_requested.add(run_id)
        if run.status is RunStatus.QUEUED:
            self.finish(run_id, RunOutcome(status=RunStatus.CANCELLED), now=now)
            return self._runs[run_id]

        # Running: the request is recorded and the run keeps reading `running`
        # until the worker reaches a stage boundary. Returning `cancelled` here
        # would be the one lie this endpoint must not tell (FR-029).
        return run
