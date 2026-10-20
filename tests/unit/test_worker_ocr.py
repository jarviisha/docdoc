"""The worker executes OCR runs, and a worker can be restricted to a kind (014)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.ingest.errors import ProviderError
from docdoc.pipeline import parse_only
from docdoc.runs.model import RunKind, RunStatus
from docdoc.runs.worker import Worker, execute_one
from tests.fixtures.run_queue import InMemoryRunQueue
from tests.support.ocr import CountingPost, paddle_registry

IMAGE = (Path(__file__).parent.parent / "fixtures" / "image" / "sample_page.png").read_bytes()
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
LEASE = timedelta(seconds=90)


@dataclass
class OcrSpec:
    blob_id: str
    tenant_id: str = "default"
    schema_identity: str | None = None
    request_id: str | None = None
    idempotency_key: str | None = None
    kind: str = "ocr"
    ocr_parser: str | None = "paddle"
    ocr_language: str | None = None


def _claimed(queue: InMemoryRunQueue, blob_id: str) -> Any:
    queue.submit(
        OcrSpec(blob_id),  # type: ignore[arg-type]
        run_id=uuid4(),
        now=NOW,
        expires_at=NOW + timedelta(hours=24),
    )
    return queue.claim(worker_id="w", now=NOW, lease=LEASE, max_attempts=3, kind=RunKind.OCR)


def _execute(tmp_path: Path, post: CountingPost, *, blob: bytes | None = IMAGE) -> Any:
    queue = InMemoryRunQueue()
    blobs = BlobStore(tmp_path)
    store = FileArtifactStore(tmp_path)
    blob_id = blobs.put(blob) if blob is not None else "sha256:" + "0" * 64
    run = _claimed(queue, blob_id)
    execute_one(
        run,
        queue=queue,
        blobs=blobs,
        store=store,
        registry=None,
        adapter=None,
        parsers=paddle_registry(post),
        now=NOW,
    )
    return queue.get(run.run_id, "default"), store


def test_ocr_run_succeeds_with_document_id(tmp_path: Path) -> None:
    run, store = _execute(tmp_path, CountingPost("INV-001"))
    assert run.status is RunStatus.SUCCEEDED
    assert store.envelope(run.processing_id) is not None
    assert [o.stage for o in run.stage_outcomes] == ["parse"]
    assert run.stage_outcomes[0].artifact_id == run.processing_id


def test_ocr_run_provider_error_fails_at_parse(tmp_path: Path) -> None:
    post = CountingPost(error=ProviderError("upstream refused", reason="auth", parser_id="paddle"))
    run, _ = _execute(tmp_path, post)
    assert run.status is RunStatus.FAILED
    assert run.failed_stage == "parse"
    assert run.error_class == "ProviderError"
    assert run.processing_id is None


def test_ocr_run_missing_blob(tmp_path: Path) -> None:
    run, _ = _execute(tmp_path, CountingPost(), blob=None)
    assert run.status is RunStatus.FAILED
    assert run.error_class == "UnknownBlob"


def test_ocr_redelivery_reuses_artifact(tmp_path: Path) -> None:
    """A lease lapsed after the parse was filed: the next attempt calls nobody."""
    post = CountingPost()
    parse_only(
        IMAGE,
        store=FileArtifactStore(tmp_path),
        parser="paddle",
        registry=paddle_registry(post),
    )
    run, _ = _execute(tmp_path, post)
    assert run.status is RunStatus.SUCCEEDED
    assert run.stage_outcomes[0].status == "reused"
    assert post.calls == 1


class _SpyQueue(InMemoryRunQueue):
    def __init__(self, worker: list[Worker]) -> None:
        super().__init__()
        self.kinds: list[RunKind | None] = []
        self._worker = worker

    def claim(self, **kwargs: Any) -> Any:  # type: ignore[override]
        self.kinds.append(kwargs.get("kind"))
        self._worker[0].stop()
        return super().claim(**kwargs)


def test_worker_kind_filter(tmp_path: Path) -> None:
    holder: list[Worker] = []
    queue = _SpyQueue(holder)
    queue.submit(
        OcrSpec("sha256:" + "0" * 64),  # type: ignore[arg-type]
        run_id=uuid4(),
        now=NOW,
        expires_at=NOW + timedelta(hours=24),
    )
    worker = Worker(
        queue=queue,
        blobs=BlobStore(tmp_path),
        store=FileArtifactStore(tmp_path),
        registry=None,
        adapter=None,
        worker_id="w",
        kind=RunKind.EXTRACTION,
    )
    holder.append(worker)
    worker._claim_forever()
    assert queue.kinds == [RunKind.EXTRACTION]
    assert queue.count_active(kind=RunKind.OCR) == 1
    assert queue.get(next(iter(queue._runs)), "default").status is RunStatus.QUEUED
