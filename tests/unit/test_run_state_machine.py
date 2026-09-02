"""The five states, their invariants, and the transitions that are refused.

Run against `InMemoryRunQueue`, which enforces the same rules the database does.
The point of testing the policy here rather than only against Postgres is that
these are questions about *rules* — and a rule only checkable with a container
running is a rule most contributors never check.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import ValidationError

from docdoc.runs.errors import RunNotCancellableError
from docdoc.runs.identity import new_run_id
from docdoc.runs.model import TERMINAL_STATES, RunOutcome, RunStatus
from tests.fixtures.run_queue import InMemoryRunQueue

T0 = datetime(2026, 8, 28, 9, 0, tzinfo=UTC)
LEASE = timedelta(seconds=90)


@dataclass
class Spec:
    tenant_id: str = "default"
    blob_id: str = "sha256:blob"
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None


def _submitted(queue: InMemoryRunQueue, **kwargs: object) -> tuple:
    run_id = new_run_id()
    run = queue.submit(
        Spec(**kwargs),  # type: ignore[arg-type]
        run_id=run_id,
        now=T0,
        expires_at=T0 + timedelta(days=30),
    )
    return run_id, run


def test_a_submitted_run_is_queued_and_names_no_result() -> None:
    queue = InMemoryRunQueue()
    _, run = _submitted(queue)

    assert run.status is RunStatus.QUEUED
    assert run.processing_id is None, "a queued run has produced no terminal artifact"
    assert run.attempts == 0
    assert run.lease_until is None


def test_claiming_moves_to_running_and_consumes_one_attempt() -> None:
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue)

    claimed = queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)

    assert claimed is not None
    assert claimed.run_id == run_id
    assert claimed.status is RunStatus.RUNNING
    assert claimed.attempts == 1
    assert claimed.lease_until == T0 + LEASE


def test_a_succeeded_run_carries_its_processing_id() -> None:
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue)
    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)

    queue.finish(
        run_id,
        RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:terminal"),
        now=T0,
    )

    run = queue.get(run_id, "default")
    assert run is not None
    assert run.status is RunStatus.SUCCEEDED
    assert run.processing_id == "sha256:terminal"
    assert run.lease_until is None, "a terminal run holds no lease"


@pytest.mark.parametrize("status", sorted(TERMINAL_STATES))
def test_no_transition_leaves_a_terminal_state(status: RunStatus) -> None:
    """data-model.md rule 7, and FR-007's half about never re-transitioning."""
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue)
    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)
    queue.finish(
        run_id,
        RunOutcome(
            status=status,
            processing_id="sha256:t" if status is RunStatus.SUCCEEDED else None,
        ),
        now=T0,
    )

    before = queue.get(run_id, "default")
    queue.finish(run_id, RunOutcome(status=RunStatus.FAILED, error_class="Other"), now=T0)
    queue.release(run_id, now=T0)

    assert queue.get(run_id, "default") == before


def test_a_terminal_run_is_never_claimed_again() -> None:
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue)
    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)
    queue.finish(run_id, RunOutcome(status=RunStatus.SUCCEEDED, processing_id="x"), now=T0)

    assert queue.claim(worker_id="w2", now=T0 + LEASE * 2, lease=LEASE, max_attempts=3) is None


def test_no_code_path_deletes_a_row() -> None:
    """FR-007. Milestone 10's retention work inherits a complete history."""
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue)

    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)
    queue.finish(run_id, RunOutcome(status=RunStatus.CANCELLED), now=T0)
    queue.release(run_id, now=T0)

    assert queue.get(run_id, "default") is not None


# -- invariants ----------------------------------------------------------------


def test_processing_id_belongs_to_success_and_to_nothing_else() -> None:
    """The one invariant a careless update could break (data-model.md)."""
    with pytest.raises(ValidationError, match="must carry its processing_id"):
        RunOutcome(status=RunStatus.SUCCEEDED)

    for status in (RunStatus.FAILED, RunStatus.CANCELLED):
        with pytest.raises(ValidationError, match="cannot carry a processing_id"):
            RunOutcome(status=status, processing_id="sha256:no")


def test_expired_is_not_a_state() -> None:
    """There is no sweep to set it, so there is no state (FR-006).

    An earlier draft listed one. A state no code path can reach lies to everyone
    who reads the enum, and it is worth a test because the pressure to add it
    back arrives with Milestone 10's retention work.
    """
    assert "expired" not in {str(s) for s in RunStatus}
    assert len(list(RunStatus)) == 5


# -- cancellation --------------------------------------------------------------


def test_cancelling_a_queued_run_stops_it_before_it_runs() -> None:
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue)

    cancelled = queue.cancel(run_id, "default", now=T0)

    assert cancelled.status is RunStatus.CANCELLED
    assert cancelled.processing_id is None, "no terminal artifact was produced (FR-033)"
    assert queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3) is None


def test_cancelling_a_running_run_records_a_request_and_says_running() -> None:
    """FR-029: the one lie this endpoint must not tell.

    A provider call already in flight completes and is billed. Reporting
    `cancelled` here would claim the run stopped when it has not.
    """
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue)
    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)

    run = queue.cancel(run_id, "default", now=T0)

    assert run.status is RunStatus.RUNNING
    assert queue.is_cancelled(run_id) is True


def test_cancelling_a_terminal_run_is_refused_naming_the_state() -> None:
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue)
    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)
    queue.finish(run_id, RunOutcome(status=RunStatus.SUCCEEDED, processing_id="x"), now=T0)

    with pytest.raises(RunNotCancellableError, match="succeeded"):
        queue.cancel(run_id, "default", now=T0)


def test_cancelling_twice_is_idempotent() -> None:
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue)

    first = queue.cancel(run_id, "default", now=T0)
    second = queue.cancel(run_id, "default", now=T0)

    assert first == second


# -- tenant scoping ------------------------------------------------------------


def test_another_tenant_sees_exactly_what_a_stranger_sees() -> None:
    """FR-066: not merely the same status, the same answer."""
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue, tenant_id="acme")

    assert queue.get(run_id, "other") is None
    assert queue.get(new_run_id(), "other") is None
