"""Issue #18: the synchronous extraction routes run the pipeline off the event loop.

The pipeline blocks end to end -- a service-backed parse, the provider call, the
store -- and the API is one process, so a run on the loop stalls every other
request it serves, `/healthz` included.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter

SOURCE = Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes()
SCHEMA = "invoice@1"


class _LoopSpyAdapter(EchoAdapter):
    """Records whether the provider call ran on the event loop's thread."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.on_loop: list[bool] = []

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        try:
            asyncio.get_running_loop()
            self.on_loop.append(True)
        except RuntimeError:
            self.on_loop.append(False)
        return super().complete(*args, **kwargs)


def _spy() -> _LoopSpyAdapter:
    adapter = _LoopSpyAdapter.from_fixtures("tests/fixtures/echo")
    assert isinstance(adapter, _LoopSpyAdapter)
    return adapter


def _client(tmp_path: Path, adapter: EchoAdapter) -> TestClient:
    return TestClient(
        build_app(
            _Deployment(
                store=FileArtifactStore(tmp_path),
                blobs=BlobStore(tmp_path),
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=adapter,
            )
        )
    )


def test_store_backed_extract_runs_the_pipeline_off_the_event_loop(tmp_path: Path) -> None:
    adapter = _spy()
    client = _client(tmp_path, adapter)
    blob_id = client.post("/v1/documents", content=SOURCE).json()["blob_id"]
    response = client.post(f"/v1/documents/{blob_id}/extract", params={"schema": SCHEMA})
    assert response.status_code == 200
    assert adapter.on_loop == [False]


def test_storeless_extract_runs_the_pipeline_off_the_event_loop(tmp_path: Path) -> None:
    adapter = _spy()
    response = _client(tmp_path, adapter).post(
        "/v1/extract", params={"schema": SCHEMA}, content=SOURCE
    )
    assert response.status_code == 200
    assert adapter.on_loop == [False]
