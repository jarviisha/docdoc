"""The five states, their invariants, and every transition `data-model.md` names.

Run against `InMemoryRunQueue`, which enforces the same rules the database does.
The point of testing the policy here rather than only against Postgres is that
these are questions about *rules* — and a rule only checkable with a container
running is a rule most contributors never check.

**This file is meant to be the whole statement of the machine, and for a while it
was not.** Three of the eight transition rules were asserted only elsewhere:
rule 3's abandonment — the word did not appear in this file at all — rule 2's
substance once `release` began returning the attempt and requiring ownership, and
rule 8's `ResultNotStored`, which arrived after the rest were written. The
coverage existed, in `test_claim_policy.py`, `test_review_findings.py` and
`test_run_queue_postgres.py`, so nothing was untested; what was wrong is that a
reader trusting the sentence above got an incomplete picture of which transitions
are pinned, and a rule that moves out of the file named for it tends not to move
back.

They are stated here now, concisely. The versions in `test_review_findings.py`
stay where they are and are not duplicates in the wasteful sense: those assert
*why a specific defect happened*, with the failure it produced; these assert
*what the machine does*. When the two go out of step it is worth knowing which
one changed.
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


# -- rule 2: `running → queued`, and what it costs -----------------------------


def test_releasing_requeues_the_run_and_returns_the_attempt() -> None:
    """Rule 2. A worker shutting down cleanly performs the transition explicitly,
    so a rolling restart costs no lease timeout (FR-043).

    **The attempt comes back**, and that is the part worth pinning. `attempts`
    bounds redelivery for one reason — a document that terminates the worker
    executing it must stop after three rather than after the whole pool (FR-021,
    SC-006) — and a graceful release is the opposite of that evidence: the worker
    is alive, it said so, and the document proved nothing. Counting it would make
    a rolling restart spend the redelivery budget of every run in flight.
    """
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue)
    claimed = queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)
    assert claimed is not None
    assert claimed.attempts == 1

    queue.release(run_id, now=T0, worker_id="w1")

    released = queue.get(run_id, "default")
    assert released is not None
    assert released.status is RunStatus.QUEUED
    assert released.attempts == 0
    assert released.lease_until is None
    assert released.worker_id is None


def test_only_the_worker_holding_a_run_may_release_it() -> None:
    """Rule 2's guard. A superseded worker that is then signalled would otherwise
    requeue a run another worker is executing — for immediate reclaim, so the same
    document is processed twice and the provider paid twice."""
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue)
    queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3)
    later = T0 + LEASE + timedelta(seconds=1)
    live = queue.claim(worker_id="w2", now=later, lease=LEASE, max_attempts=3)
    assert live is not None

    queue.release(run_id, now=later, worker_id="w1")

    current = queue.get(run_id, "default")
    assert current is not None
    assert current.status is RunStatus.RUNNING
    assert current.worker_id == "w2"
    assert current.attempts == live.attempts


# -- rule 3: `running`/`queued` → `failed/abandoned` ---------------------------


def test_a_run_that_keeps_losing_its_worker_comes_to_rest_as_abandoned() -> None:
    """Rule 3, and the word is chosen rather than generic (FR-038).

    The failure it bounds is the poison document: one that terminates the process
    handling it, is redelivered, terminates the next worker, and takes the pool
    one process at a time. Nothing logs an error, because nothing survives to log
    one. Three, because a document that terminates three workers will terminate
    thirty.
    """
    queue = InMemoryRunQueue()
    run_id, _ = _submitted(queue)

    at = T0
    for _ in range(3):
        assert queue.claim(worker_id="w", now=at, lease=LEASE, max_attempts=3) is not None
        at += LEASE + timedelta(seconds=1)

    assert queue.claim(worker_id="w", now=at, lease=LEASE, max_attempts=3) is None

    run = queue.get(run_id, "default")
    assert run is not None
    assert run.status is RunStatus.FAILED
    assert run.error_class == "RunAbandonedError"
    assert run.processing_id is None


def test_a_queued_run_at_the_limit_is_abandoned_rather_than_stranded() -> None:
    """Rule 3's other half, and the invariant it exists to keep true.

    The claim requires `attempts < max_attempts` and the sweep once looked only at
    `running` rows, so a run sitting in `queued` at the limit matched neither — it
    was invisible to every worker and to the sweep, for ever, with the caller
    polling and no `RunAbandonedError` ever recorded.

    **Every non-terminal run is either claimable or abandonable.** That is the
    invariant, and it is a property of the two predicates together rather than of
    either alone, which is why nothing caught its absence.
    """
    queue = InMemoryRunQueue()
    run_id, run = _submitted(queue)
    # Reached directly rather than through `release`, because the invariant must
    # hold however a run arrives in this shape.
    queue._runs[run_id] = run.model_copy(update={"attempts": 3})

    assert queue.claim(worker_id="w1", now=T0, lease=LEASE, max_attempts=3) is None

    stranded = queue.get(run_id, "default")
    assert stranded is not None
    assert stranded.status is RunStatus.FAILED
    assert stranded.error_class == "RunAbandonedError"


# -- rule 8: `running → failed` with `ResultNotStored` -------------------------


def test_a_succeeded_run_whose_result_did_not_survive_is_not_succeeded() -> None:
    """Rule 8. The mirror of rule 4: that one is the terminal failure which
    reached *no* stage, this is the one which reached them all.

    Both artifact stores drop a failed write and continue (FR-063), which is right
    for the three intermediate stages — they are a cache — and wrong for the last
    one, whose id becomes the `processing_id` and is the only handle the caller is
    given. A dropped write there produced `succeeded` plus an identity that
    `GET /v1/jobs/{id}/result` answers `unknown` about, for ever.

    Asserted against the check itself rather than through a worker, because the
    rule is about the *outcome* and driving a pipeline to reach it would test four
    stages to observe one decision.
    """
    from docdoc.runs.worker import _demand_the_result_is_retrievable

    class _Lost:
        """A store that took the write and does not have it."""

        def envelope(self, artifact_id: str) -> None:
            return None

    checked = _demand_the_result_is_retrievable(
        _Lost(),
        RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:" + "a" * 64),
    )

    assert checked.status is RunStatus.FAILED
    assert checked.error_class == "ResultNotStored"
    assert checked.failed_stage == "validation", (
        "the stage that ran is named, because that is where the result came from"
    )
    assert checked.processing_id is None, (
        "a failed run carries no processing_id — the check constraint enforces it, "
        "and it is what keeps the two identities of ADR-0013 distinguishable"
    )
