"""A worker re-reads the registry when a claimed run names a schema it has not seen."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters import EchoAdapter
from docdoc.runs.console_schemas import ConsoleSchema, merged_registry
from docdoc.runs.errors import RunStateUnavailableError
from docdoc.runs.model import RunStatus
from docdoc.runs.worker import Worker
from tests.fixtures.run_queue import InMemoryRunQueue
from tests.fixtures.schema_store import InMemorySchemaStore

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
DOCUMENT = Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes()


@dataclass
class _Spec:
    blob_id: str
    schema_identity: str = "invoice@3"
    tenant_id: str = "default"
    request_id: str | None = None
    idempotency_key: str | None = None
    kind: str = "extraction"


def test_worker_reloads_registry_on_unknown_identity(tmp_path: Path) -> None:
    files = SchemaRegistry.from_paths([Path("schemas")])
    document = json.loads(Path("schemas/invoice@2.json").read_text(encoding="utf-8"))
    store = InMemorySchemaStore()
    store.insert(
        ConsoleSchema(
            name="invoice",
            version=3,
            document={**document, "version": 3},
            prompt=Path("schemas/prompts/invoice@2.md").read_text(encoding="utf-8"),
            base="invoice@2",
            created_at=NOW,
            created_by="alice",
        )
    )
    echo = tmp_path / "echo"
    echo.mkdir()
    (echo / "invoice@3.json").write_text(
        Path("tests/fixtures/echo/invoice@2.json").read_text(encoding="utf-8"), encoding="utf-8"
    )
    reloads: list[int] = []

    def reload() -> Any:
        reloads.append(1)
        return merged_registry(files, store)

    queue = InMemoryRunQueue()
    blobs = BlobStore(tmp_path)
    run_id = uuid4()
    queue.submit(
        _Spec(blobs.put(DOCUMENT)),  # type: ignore[arg-type]
        run_id=run_id,
        now=NOW,
        expires_at=NOW + timedelta(hours=24),
    )
    run = queue.claim(worker_id="w", now=NOW, lease=timedelta(seconds=90), max_attempts=3)
    worker = Worker(
        queue=queue,
        blobs=blobs,
        store=FileArtifactStore(tmp_path),
        registry=files,
        adapter=EchoAdapter.from_fixtures(echo),
        worker_id="w",
        reload_registry=reload,
    )

    worker._execute_with_heartbeat(run)

    assert queue.get(run_id, "default").status is RunStatus.SUCCEEDED
    assert len(reloads) == 1


def test_reload_during_a_database_outage_leaves_the_run_for_retry(tmp_path: Path) -> None:
    files = SchemaRegistry.from_paths([Path("schemas")])

    class Down:
        def all(self) -> tuple[ConsoleSchema, ...]:
            raise RunStateUnavailableError("down")

    queue = InMemoryRunQueue()
    blobs = BlobStore(tmp_path)
    run_id = uuid4()
    queue.submit(
        _Spec(blobs.put(DOCUMENT)),  # type: ignore[arg-type]
        run_id=run_id,
        now=NOW,
        expires_at=NOW + timedelta(hours=24),
    )
    run = queue.claim(worker_id="w", now=NOW, lease=timedelta(seconds=90), max_attempts=3)
    worker = Worker(
        queue=queue,
        blobs=blobs,
        store=FileArtifactStore(tmp_path),
        registry=files,
        adapter=EchoAdapter.from_fixtures(tmp_path),
        worker_id="w",
        reload_registry=lambda: merged_registry(files, Down(), strict=True),  # type: ignore[arg-type]
    )

    with pytest.raises(RunStateUnavailableError):
        worker._execute_with_heartbeat(run)

    assert worker._registry is files
    assert queue.get(run_id, "default").status is RunStatus.RUNNING


def test_worker_starting_with_db_down_does_not_serve_a_shadowed_file(tmp_path: Path) -> None:
    from docdoc.cli.commands.worker import _first_registry

    files = SchemaRegistry.from_paths([Path("schemas")])
    document = json.loads(Path("schemas/invoice@2.json").read_text(encoding="utf-8"))
    store = InMemorySchemaStore()
    down = True

    class Flaky:
        def all(self) -> tuple[ConsoleSchema, ...]:
            if down:
                raise RunStateUnavailableError("down")
            return store.all()

    class _Settings:
        def registry(self, console: Any = None, *, strict: bool = False) -> Any:
            return merged_registry(files, console, strict=strict)

    flaky = Flaky()
    registry = _first_registry(_Settings(), flaky)  # type: ignore[arg-type]
    assert "invoice@2" not in registry

    down = False
    store.insert(
        ConsoleSchema(
            name="invoice",
            version=2,
            document=document,
            prompt="console prompt",
            base=None,
            created_at=NOW,
            created_by="alice",
        )
    )
    queue = InMemoryRunQueue()
    blobs = BlobStore(tmp_path)
    run_id = uuid4()
    queue.submit(
        _Spec(blobs.put(DOCUMENT), schema_identity="invoice@2"),  # type: ignore[arg-type]
        run_id=run_id,
        now=NOW,
        expires_at=NOW + timedelta(hours=24),
    )
    run = queue.claim(worker_id="w", now=NOW, lease=timedelta(seconds=90), max_attempts=3)
    worker = Worker(
        queue=queue,
        blobs=blobs,
        store=FileArtifactStore(tmp_path),
        registry=registry,
        adapter=EchoAdapter.from_fixtures(Path("tests/fixtures/echo")),
        worker_id="w",
        reload_registry=lambda: _Settings().registry(flaky, strict=True),
    )

    worker._execute_with_heartbeat(run)

    assert worker._registry.resolve("invoice@2").source == "console"
