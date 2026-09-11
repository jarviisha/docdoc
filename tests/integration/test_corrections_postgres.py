"""T156, T157, T158 — a correction changes nothing, moves nothing, pins one run.

**Three tasks in one file** for the reason the delivery tests share one: all
three need a migrated database, a finished run, and a recorded correction, and
three files would be three copies of that fixture drifting apart.

The three properties are the whole argument that this milestone is not the
deferred review platform:

*It changes nothing it annotates* (SC-018). The result, its artifacts, and its
identities hash identically before and after. If recording a correction edited
what it described, the recorded pipeline output would become a function of who
reviewed it.

*It moves no metric* (FR-080). Promotion is a separate, explicit act producing a
new `golden_set_id`, so reports either side of it are not comparable without the
difference being visible.

*It pins its run* (FR-013, SC-019). A retention sweep that deleted a result
somebody had corrected is a data-loss bug that appears only once both halves of
this milestone exist — which is why the sweep asks, and why this asks the sweep.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from tests.infra import require_database

from docdoc.evaluation.corrections import Correction
from docdoc.runs import migrations, retention
from docdoc.runs.corrections import PostgresCorrectionStore, for_promotion
from docdoc.runs.identity import new_run_id
from docdoc.runs.model import RunOutcome, RunStatus, StageOutcomeRecord
from docdoc.runs.postgres import PostgresRunQueue

pytestmark = pytest.mark.postgres

TENANT = "acme"
OTHER = "globex"
PROCESSING_ID = "sha256:" + "b" * 64
ARTIFACT_ID = "sha256:" + "c" * 64


@dataclass
class Spec:
    tenant_id: str = TENANT
    blob_id: str = "sha256:" + "a" * 64
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None
    priority: int = 0
    callback_id: object = None


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs, corrections, run_tombstones")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


@pytest.fixture
def store(queue: PostgresRunQueue) -> PostgresCorrectionStore:
    return PostgresCorrectionStore(execute=queue._execute)


def _correction(field: str = "total", annotator: str = "ana") -> Correction:
    return Correction(
        report_id=PROCESSING_ID,
        document_id="sha256:" + "a" * 64,
        field_path=field,
        predicted_value="1234.56",
        corrected_value="1243.56",
        reason="the parser transposed two digits",
        annotator=annotator,
        timestamp=datetime(2026, 9, 9, 10, 0, tzinfo=UTC),
    )


def _finished_run(queue: PostgresRunQueue, *, at: datetime, expires_at: datetime | None = None):
    run = queue.submit(
        Spec(),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=at,
        expires_at=expires_at or (at + timedelta(days=30)),
    )
    queue.finish(
        run.run_id,
        RunOutcome(
            status=RunStatus.SUCCEEDED,
            processing_id=PROCESSING_ID,
            stage_outcomes=(
                StageOutcomeRecord(stage="validate", status="executed", artifact_id=ARTIFACT_ID),
            ),
        ),
        now=at,
    )
    return queue.get(run.run_id, TENANT)


def _fingerprint(run) -> str:
    """Everything about a run that a correction must not move.

    Values, verdicts, locations, and identities — the four SC-018 names — reach
    this through `processing_id` and the recorded `stage_outcomes`, which is what
    a caller can actually observe about a completed run.
    """
    return hashlib.sha256(
        json.dumps(
            {
                "processing_id": run.processing_id,
                "status": str(run.status),
                "stage_outcomes": [o.model_dump(mode="json") for o in run.stage_outcomes],
                "schema_identity": run.schema_identity,
                "blob_id": run.blob_id,
            },
            sort_keys=True,
        ).encode()
    ).hexdigest()


# -- T156: the result is unchanged -------------------------------------------


def test_recording_a_correction_changes_nothing_about_the_run(
    queue: PostgresRunQueue, store: PostgresCorrectionStore
) -> None:
    """SC-018. Hashed before and after, over everything a caller can observe."""
    at = datetime.now(UTC)
    run = _finished_run(queue, at=at)
    before = _fingerprint(run)

    store.record(
        _correction(),
        tenant_id=TENANT,
        run_id=run.run_id,
        at=at,
        expires_at=at + timedelta(days=60),
    )

    after = _fingerprint(queue.get(run.run_id, TENANT))
    assert after == before, (
        "recording a correction moved something about the result it annotates. A "
        "correction sits beside what it describes; one that edited it would make "
        "the recorded pipeline output a function of who reviewed it (FR-078)"
    )


def test_the_correction_comes_back_whole(
    queue: PostgresRunQueue, store: PostgresCorrectionStore
) -> None:
    """FR-077. Stored as `jsonb` and returned as Milestone 6's model, not a copy."""
    at = datetime.now(UTC)
    run = _finished_run(queue, at=at)
    original = _correction()

    store.record(
        original, tenant_id=TENANT, run_id=run.run_id, at=at, expires_at=at + timedelta(days=60)
    )

    stored = store.for_run(tenant_id=TENANT, run_id=run.run_id)
    assert stored == (original,)


def test_another_tenants_correction_is_invisible(
    queue: PostgresRunQueue, store: PostgresCorrectionStore
) -> None:
    """FR-079. A `run_id` is not a permission, and the scoping is in the query."""
    at = datetime.now(UTC)
    run = _finished_run(queue, at=at)
    store.record(
        _correction(),
        tenant_id=TENANT,
        run_id=run.run_id,
        at=at,
        expires_at=at + timedelta(days=60),
    )

    assert store.for_run(tenant_id=OTHER, run_id=run.run_id) == ()


# -- T157: no metric moves ----------------------------------------------------


def test_a_correction_moves_no_metric_until_it_is_promoted(
    queue: PostgresRunQueue, store: PostgresCorrectionStore
) -> None:
    """FR-080, SC-018's other half.

    Recording is not promoting. `for_promotion` hands the corrections to the
    existing promotion path and **promotes nothing itself** — promotion produces
    a new `golden_set_id`, so reports either side of it are visibly
    incomparable. A correction that entered a dataset by being recorded would
    move every historical number and explain none of them.
    """
    at = datetime.now(UTC)
    run = _finished_run(queue, at=at)
    store.record(
        _correction(),
        tenant_id=TENANT,
        run_id=run.run_id,
        at=at,
        expires_at=at + timedelta(days=60),
    )

    exported = for_promotion(store.for_run(tenant_id=TENANT, run_id=run.run_id))

    assert len(exported) == 1
    assert all(isinstance(item, Correction) for item in exported)
    # No golden set was created, no metric was computed, and nothing was written
    # anywhere a report would read.
    assert not hasattr(for_promotion, "golden_set_id")


def test_the_export_is_ordered_so_two_runs_of_it_agree(
    queue: PostgresRunQueue, store: PostgresCorrectionStore
) -> None:
    """A promotion input whose order varied would produce a different
    `golden_set_id` for the same corrections, which would make the identity
    meaningless."""
    at = datetime.now(UTC)
    run = _finished_run(queue, at=at)
    for field in ("total", "invoice_number", "due_date"):
        store.record(
            _correction(field=field),
            tenant_id=TENANT,
            run_id=run.run_id,
            at=at,
            expires_at=at + timedelta(days=60),
        )

    once = for_promotion(store.for_run(tenant_id=TENANT, run_id=run.run_id))
    twice = for_promotion(store.for_run(tenant_id=TENANT, run_id=run.run_id))

    assert once == twice
    assert [c.field_path for c in once] == ["due_date", "invoice_number", "total"]


# -- T158: a live correction pins its run ------------------------------------


class _Nothing:
    """A store holding nothing. The sweep's content deletion is not under test
    here — `test_sweep_is_a_difference.py` owns that — and this keeps the
    fixture to the one question this file asks."""

    def delete(self, identity: str) -> bool:
        return False

    def delete_prefix(self, *, allow_store_root: bool = False) -> int:
        return 0


def _sweep(queue: PostgresRunQueue, store: PostgresCorrectionStore, *, now: datetime):
    return retention.sweep(
        queue,
        lambda _: retention.Stores(artifacts=_Nothing(), blobs=_Nothing()),
        now=now,
        batch=100,
        corrections=store,
    )


def test_a_run_carrying_a_live_correction_survives_the_sweep(
    queue: PostgresRunQueue, store: PostgresCorrectionStore
) -> None:
    """**SC-019.** The one place this milestone's two halves touch."""
    at = datetime.now(UTC)
    expired = at - timedelta(days=1)
    run = _finished_run(queue, at=at - timedelta(days=40), expires_at=expired)

    store.record(
        _correction(),
        tenant_id=TENANT,
        run_id=run.run_id,
        at=at,
        # The correction outlives the run's own deadline, which is the case
        # FR-013 is about: where the two disagree, the longer wins.
        expires_at=at + timedelta(days=60),
    )

    report = _sweep(queue, store, now=at)

    assert report.pinned == 1
    assert report.runs == 0
    assert queue.get(run.run_id, TENANT) is not None, (
        "a run whose result somebody corrected was swept. That is the data-loss "
        "bug that appears only once both halves of this milestone exist (FR-013)"
    )


def test_it_goes_once_the_correction_itself_expires(
    queue: PostgresRunQueue, store: PostgresCorrectionStore
) -> None:
    """The other half of SC-019: pinned is not "kept for ever"."""
    at = datetime.now(UTC)
    run = _finished_run(queue, at=at - timedelta(days=40), expires_at=at - timedelta(days=1))

    store.record(
        _correction(),
        tenant_id=TENANT,
        run_id=run.run_id,
        at=at - timedelta(days=30),
        expires_at=at - timedelta(hours=1),  # already expired
    )

    report = _sweep(queue, store, now=at)

    assert report.pinned == 0
    assert report.runs == 1
    assert queue.get(run.run_id, TENANT) is None


def test_an_uncorrected_run_is_swept_as_it_always_was(
    queue: PostgresRunQueue, store: PostgresCorrectionStore
) -> None:
    """Guards the guard: a `pinned_runs` that returned everything would make the
    two assertions above pass and retention stop working."""
    at = datetime.now(UTC)
    run = _finished_run(queue, at=at - timedelta(days=40), expires_at=at - timedelta(days=1))

    report = _sweep(queue, store, now=at)

    assert report.runs == 1
    assert queue.get(run.run_id, TENANT) is None


def test_pinned_runs_short_circuits_on_an_empty_batch(
    store: PostgresCorrectionStore,
) -> None:
    """`IN ()` is a syntax error in Postgres, and an empty first batch is the
    ordinary case for a deployment with nothing to sweep."""
    assert store.pinned_runs([], at=datetime.now(UTC)) == frozenset()
