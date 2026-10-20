"""The queue and kinds: claim by kind, live counts, OCR expiry from finish (014).

Shared with `tests/integration/test_queue_kinds_postgres.py`, which runs the same
cases against `PostgresRunQueue` -- the fake is only worth its tests while it
answers as the real queue does.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from docdoc.runs.model import RunKind, RunOutcome, RunStatus
from tests.fixtures.run_queue import InMemoryRunQueue

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
LEASE = timedelta(seconds=90)
DAY = timedelta(hours=24)


@dataclass
class Spec:
    tenant_id: str = "default"
    blob_id: str = "sha256:" + "b" * 64
    schema_identity: str | None = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None
    kind: str = "extraction"
    ocr_parser: str | None = None
    ocr_language: str | None = None


def ocr(tenant_id: str = "default") -> Spec:
    return Spec(tenant_id=tenant_id, schema_identity=None, kind="ocr", ocr_parser="paddle")


def submit(queue: Any, spec: Spec, *, at: datetime = NOW, keep: timedelta = DAY) -> Any:
    return queue.submit(spec, run_id=uuid4(), now=at, expires_at=at + keep)


def claim(queue: Any, *, at: datetime = NOW, kind: RunKind | None = None) -> Any:
    return queue.claim(worker_id="w", now=at, lease=LEASE, max_attempts=3, kind=kind)


def succeed(queue: Any, run: Any, *, at: datetime) -> None:
    assert queue.finish(
        run.run_id,
        RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:" + "d" * 64),
        now=at,
        worker_id=run.worker_id,
    )


# Each case takes the queue as an argument so the Postgres file can reuse it.


def case_claim_kind_filters(queue: Any) -> None:
    extraction = submit(queue, Spec())
    job = submit(queue, ocr(), at=NOW + timedelta(seconds=1))
    later = NOW + timedelta(seconds=2)
    assert claim(queue, at=later, kind=RunKind.OCR).run_id == job.run_id
    assert claim(queue, at=later, kind=RunKind.OCR) is None
    assert claim(queue, at=later, kind=None).run_id == extraction.run_id


def case_extraction_worker_never_claims_ocr(queue: Any) -> None:
    submit(queue, ocr())
    assert claim(queue, kind=RunKind.EXTRACTION) is None


def case_count_active_by_kind_and_tenant(queue: Any) -> None:
    for _ in range(3):
        submit(queue, ocr("a"))
    running = claim(queue, kind=RunKind.OCR)
    assert running is not None
    submit(queue, ocr("b"))
    submit(queue, Spec(tenant_id="a"))
    done = submit(queue, ocr("a"))
    queue.finish(done.run_id, RunOutcome(status=RunStatus.CANCELLED), now=NOW)

    assert queue.count_active(kind=RunKind.OCR, tenant_id="a") == 3
    assert queue.count_active(kind=RunKind.OCR) == 4
    assert queue.count_active(kind=RunKind.EXTRACTION) == 1


def case_ocr_expiry_counts_from_finish(queue: Any) -> None:
    job = submit(queue, ocr())
    extraction = submit(queue, Spec())
    finished = NOW + timedelta(hours=30)
    for kind in (RunKind.OCR, RunKind.EXTRACTION):
        succeed(queue, claim(queue, at=finished, kind=kind), at=finished)

    assert queue.get(job.run_id, "default").expires_at == finished + DAY
    assert queue.get(extraction.run_id, "default").expires_at == NOW + DAY


def case_expiring_kind_filter(queue: Any) -> None:
    job = submit(queue, ocr())
    submit(queue, Spec())
    for kind in (RunKind.OCR, RunKind.EXTRACTION):
        succeed(queue, claim(queue, kind=kind), at=NOW)
    long_after = NOW + timedelta(days=60)

    assert [r.run_id for r in queue.expiring(now=long_after, limit=10, kind=RunKind.OCR)] == [
        job.run_id
    ]
    assert len(queue.expiring(now=long_after, limit=10)) == 2


CASES = (
    case_claim_kind_filters,
    case_extraction_worker_never_claims_ocr,
    case_count_active_by_kind_and_tenant,
    case_ocr_expiry_counts_from_finish,
    case_expiring_kind_filter,
)


def test_claim_kind_filters() -> None:
    case_claim_kind_filters(InMemoryRunQueue())


def test_extraction_worker_never_claims_ocr() -> None:
    case_extraction_worker_never_claims_ocr(InMemoryRunQueue())


def test_count_active_by_kind_and_tenant() -> None:
    case_count_active_by_kind_and_tenant(InMemoryRunQueue())


def test_ocr_expiry_counts_from_finish() -> None:
    case_ocr_expiry_counts_from_finish(InMemoryRunQueue())


def test_expiring_kind_filter() -> None:
    case_expiring_kind_filter(InMemoryRunQueue())


def case_abandoned_ocr_expiry_counts_from_abandonment(queue: Any) -> None:
    """Review finding 7: an OCR run abandoned after a long wait is not swept unseen."""
    job = submit(queue, ocr())
    late = NOW + timedelta(hours=30)
    for attempt in range(3):
        at = late + timedelta(minutes=10 * attempt)
        assert claim(queue, at=at, kind=RunKind.OCR) is not None
    abandoned_at = late + timedelta(hours=1)
    assert claim(queue, at=abandoned_at, kind=RunKind.OCR) is None

    run = queue.get(job.run_id, "default")
    assert run.status is RunStatus.FAILED
    assert run.error_class == "RunAbandonedError"
    assert run.expires_at == abandoned_at + DAY


CASES = (*CASES, case_abandoned_ocr_expiry_counts_from_abandonment)


def test_abandoned_ocr_expiry_counts_from_abandonment() -> None:
    case_abandoned_ocr_expiry_counts_from_abandonment(InMemoryRunQueue())
