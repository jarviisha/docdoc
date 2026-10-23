"""A worker resolves the claimed run's tenant's model adapter at run start."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters import EchoAdapter
from docdoc.runs.errors import RunStateUnavailableError
from docdoc.runs.model import RunStatus
from docdoc.runs.providers import resolve_adapter
from docdoc.runs.worker import Worker
from tests.fixtures.provider_store import InMemoryProviderStore
from tests.fixtures.run_queue import InMemoryRunQueue

if TYPE_CHECKING:
    from docdoc.runs.model import Run

NOW = datetime(2026, 10, 23, 9, 0, tzinfo=UTC)
DOCUMENT = Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes()
FIXTURES = Path("tests/fixtures/echo")


@dataclass
class _Spec:
    blob_id: str
    tenant_id: str
    schema_identity: str = "invoice@2"
    request_id: str | None = None
    idempotency_key: str | None = None
    kind: str = "extraction"


class _Recording(EchoAdapter):
    calls: list[str]

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        self.calls.append(self.model_id)
        return super().complete(*args, **kwargs)


def _adapter(model_id: str, calls: list[str]) -> EchoAdapter:
    responses = EchoAdapter.from_fixtures(FIXTURES)._responses
    adapter = _Recording(responses, model_id=model_id)
    adapter.calls = calls
    return adapter


def _claimed(queue: InMemoryRunQueue, blobs: BlobStore, tenant_id: str) -> Run:
    queue.submit(
        _Spec(blobs.put(DOCUMENT), tenant_id),  # type: ignore[arg-type]
        run_id=uuid4(),
        now=NOW,
        expires_at=NOW + timedelta(hours=24),
    )
    run = queue.claim(worker_id="w", now=NOW, lease=timedelta(seconds=90), max_attempts=3)
    assert run is not None
    return run


def _worker(queue: InMemoryRunQueue, tmp_path: Path, adapter_for: Any) -> Worker:
    return Worker(
        queue=queue,
        blobs=BlobStore(tmp_path),
        store=FileArtifactStore(tmp_path),
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=None,
        worker_id="w",
        adapter_for=adapter_for,
    )


def test_run_uses_its_tenants_adapter(tmp_path: Path) -> None:
    calls: list[str] = []
    queue = InMemoryRunQueue()
    blobs = BlobStore(tmp_path)
    worker = _worker(queue, tmp_path, lambda tenant: _adapter(f"model-{tenant}", calls))

    for tenant in ("acme", "globex"):
        run = _claimed(queue, blobs, tenant)
        worker._execute_with_heartbeat(run)
        assert queue.get(run.run_id, tenant).status is RunStatus.SUCCEEDED

    assert calls == ["model-acme", "model-globex"]


def test_unusable_tenant_adapter_fails_the_run_with_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("DOCDOC_MODEL_ADAPTERS", raising=False)
    providers = InMemoryProviderStore()
    # A saved key with no sealer to open it: gemini is unavailable, and echo is
    # never auto-selected, so nothing answers.
    providers.set_adapter(
        "acme", "gemini", model=None, key=(b"sealed", "abcd"), by="alice", now=NOW
    )
    queue = InMemoryRunQueue()
    run = _claimed(queue, BlobStore(tmp_path), "acme")
    worker = _worker(queue, tmp_path, lambda tenant: resolve_adapter(tenant, providers, None))

    worker._execute_with_heartbeat(run)

    failed = queue.get(run.run_id, "acme")
    assert failed.status is RunStatus.FAILED
    assert failed.failed_stage == "extract"
    assert failed.error_class == "ModelProviderError"
    assert failed.error_reason == "unavailable"


def test_provider_db_blip_retries_not_fails(tmp_path: Path) -> None:
    class Down:
        def scope(self, tenant_id: str) -> Any:
            raise RunStateUnavailableError("down")

    queue = InMemoryRunQueue()
    run = _claimed(queue, BlobStore(tmp_path), "acme")
    worker = _worker(queue, tmp_path, lambda tenant: resolve_adapter(tenant, Down(), None))  # type: ignore[arg-type]

    with pytest.raises(RunStateUnavailableError):
        worker._execute_with_heartbeat(run)

    assert queue.get(run.run_id, "acme").status is RunStatus.RUNNING


def test_ocr_run_does_not_resolve_an_adapter(tmp_path: Path) -> None:
    def refuse(tenant: str) -> Any:
        raise AssertionError("an OCR run resolved an adapter")

    queue = InMemoryRunQueue()
    blobs = BlobStore(tmp_path)
    queue.submit(
        _Spec(blobs.put(DOCUMENT), "acme", schema_identity=None, kind="ocr"),  # type: ignore[arg-type]
        run_id=uuid4(),
        now=NOW,
        expires_at=NOW + timedelta(hours=24),
    )
    run = queue.claim(worker_id="w", now=NOW, lease=timedelta(seconds=90), max_attempts=3)
    assert run is not None

    _worker(queue, tmp_path, refuse)._execute_with_heartbeat(run)

    assert queue.get(run.run_id, "acme").status is RunStatus.SUCCEEDED


def test_worker_starts_without_env_key_when_db_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("psycopg", reason="the worker needs docdoc[postgres]")
    import argparse

    from docdoc.cli.commands import worker as command
    from docdoc.extraction.errors import ModelProviderError

    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    built: dict[str, Any] = {}

    class _Worker:
        def __init__(self, **kwargs: Any) -> None:
            built.update(kwargs)

        def install_signal_handlers(self) -> None:
            pass

        def run_forever(self) -> None:
            pass

    monkeypatch.setattr(command, "Worker", _Worker)

    class _Settings:
        run_database_url = "postgresql://docdoc:docdoc@localhost:1/docdoc"
        has_store = True

        def blobs(self) -> object:
            return "blobs"

        def store(self) -> object:
            return "store"

        def registry(self, console: object = None, *, strict: bool = False) -> object:
            return "registry"

        def adapter(self, *args: Any, **kwargs: Any) -> object:
            raise ModelProviderError("no key", reason="unavailable", adapter_id="gemini")

        def limits(self) -> object:
            return "limits"

        def stores_for(self, tenant_id: str) -> tuple[object, object]:
            return ("store", "blobs")

    command.run(argparse.Namespace(), _Settings())  # type: ignore[arg-type]

    assert callable(built["adapter_for"])
