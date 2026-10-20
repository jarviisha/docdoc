"""OCR results are swept a day after finishing, with run retention off (014, ADR-0022).

Shared with `tests/integration/test_ocr_retention_postgres.py`: each case takes
the queue, so the sweep's kind filter is checked against the real `expiring`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.runs import identity, maintenance, retention
from docdoc.runs.model import RunKind, RunOutcome, RunStatus, StageOutcomeRecord
from tests.fixtures.run_queue import InMemoryRunQueue

SUBMITTED = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
DAY = timedelta(hours=24)
LEASE = timedelta(seconds=90)
BLOB = "sha256:" + "a" * 64


@dataclass
class Spec:
    tenant_id: str = "default"
    blob_id: str = BLOB
    schema_identity: str | None = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None
    kind: str = "extraction"
    ocr_parser: str | None = None
    ocr_language: str | None = None


def _finished(queue: Any, spec: Spec, *, at: datetime, keep: timedelta) -> Any:
    run = queue.submit(spec, run_id=uuid4(), now=SUBMITTED, expires_at=SUBMITTED + keep)
    claimed = queue.claim(
        worker_id="w", now=at, lease=LEASE, max_attempts=3, kind=RunKind(spec.kind)
    )
    assert claimed.run_id == run.run_id
    artifact = "sha256:" + "d" * 64
    queue.finish(
        run.run_id,
        RunOutcome(
            status=RunStatus.SUCCEEDED,
            processing_id=artifact,
            stage_outcomes=(
                StageOutcomeRecord(stage="parse", status="executed", artifact_id=artifact),
            ),
        ),
        now=at,
        worker_id="w",
    )
    return run


def _ocr() -> Spec:
    return Spec(schema_identity=None, kind="ocr", ocr_parser="paddle")


def _tick(queue: Any, tmp_path: Path, now: datetime) -> maintenance.TickReport:
    def stores_for(tenant_id: str) -> retention.Stores:
        return retention.Stores(
            artifacts=FileArtifactStore(tmp_path, tenant_id=tenant_id),
            blobs=BlobStore(tmp_path, tenant_id=tenant_id),
        )

    deps = maintenance.MaintenanceDeps(queue=queue, stores_for=stores_for)
    return maintenance.tick(deps, now=now, retention_period=None, batch=10)


def case_tick_sweeps_ocr_with_run_retention_off(queue: Any, tmp_path: Path) -> None:
    finished = SUBMITTED + timedelta(hours=1)
    job = _finished(queue, _ocr(), at=finished, keep=DAY)
    # An extraction run long past its deadline: run retention is off, so it stays.
    extraction = _finished(queue, Spec(), at=finished, keep=timedelta(hours=1))

    report = _tick(queue, tmp_path, finished + DAY + timedelta(hours=1))

    assert report.swept.runs == 1
    assert queue.get(job.run_id, "default") is None
    assert queue.tombstone(job.run_id, "default").policy == "retention"
    assert queue.get(extraction.run_id, "default") is not None


def case_tick_keeps_fresh_ocr(queue: Any, tmp_path: Path) -> None:
    finished = SUBMITTED + timedelta(hours=30)
    job = _finished(queue, _ocr(), at=finished, keep=DAY)
    # 23 h after finishing and 53 h after submission: kept, because the day
    # counts from finishing.
    _tick(queue, tmp_path, finished + timedelta(hours=23))
    assert queue.get(job.run_id, "default") is not None


def case_shared_blob_survives(queue: Any, tmp_path: Path) -> None:
    blobs = BlobStore(tmp_path)
    blob_id = blobs.put(b"one document, two runs")
    finished = SUBMITTED + timedelta(hours=1)
    _finished(
        queue,
        Spec(blob_id=blob_id, schema_identity=None, kind="ocr", ocr_parser="paddle"),
        at=finished,
        keep=DAY,
    )
    queue.submit(
        Spec(blob_id=blob_id), run_id=uuid4(), now=SUBMITTED, expires_at=SUBMITTED + DAY * 30
    )

    report = _tick(queue, tmp_path, finished + DAY * 2)

    assert report.swept.runs == 1
    assert blobs.get(blob_id) is not None


CASES = (
    case_tick_sweeps_ocr_with_run_retention_off,
    case_tick_keeps_fresh_ocr,
    case_shared_blob_survives,
)


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.__name__)
def test_case(case: Any, tmp_path: Path) -> None:
    case(InMemoryRunQueue(), tmp_path)


def test_configured_ocr_retention_default_and_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(identity.OCR_RETENTION_HOURS_ENV, raising=False)
    assert identity.configured_ocr_retention() == DAY
    monkeypatch.setenv(identity.OCR_RETENTION_HOURS_ENV, "2")
    assert identity.configured_ocr_retention() == timedelta(hours=2)
    # An unreadable value falls back, as every tuning knob in `identity` does.
    monkeypatch.setenv(identity.OCR_RETENTION_HOURS_ENV, "0")
    assert identity.configured_ocr_retention() == DAY
    with pytest.raises(ValueError, match="--ocr-retention-hours"):
        identity.configured_ocr_retention(0)
