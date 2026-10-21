"""`page_angles` on the console's OCR result, and never on the tenant's (020)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue
from tests.support.console import PLAIN_KEY, Ring, accounts_with_alice, sign_in
from tests.support.ocr import CountingPost, paddle_registry, response

from docdoc.api.app import _Deployment, build_app
from docdoc.ingest.parsers.paddlex import OcrEndpoint
from docdoc.pipeline.orientation import orientation_id_for
from docdoc.runs.model import Run, RunKind
from docdoc.runs.worker import execute_one

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
IMAGE = (Path(__file__).parent.parent / "fixtures" / "image" / "sample_page.png").read_bytes()


def _turned(raw_angle: int) -> Any:
    def post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
        answer = response("INV-777")
        answer["result"]["ocrResults"][0]["prunedResult"]["doc_preprocessor_res"] = {
            "angle": raw_angle
        }
        return answer

    return post


class _OcrSpec:
    def __init__(self, tenant_id: str, blob_id: str) -> None:
        self.tenant_id = tenant_id
        self.blob_id = blob_id

    schema_identity = None
    request_id = None
    idempotency_key = None
    kind = "ocr"
    ocr_parser = "paddle"
    ocr_language = None


@pytest.fixture
def queue() -> InMemoryRunQueue:
    return InMemoryRunQueue()


@pytest.fixture
def deployment(tmp_path: Path, queue: InMemoryRunQueue) -> _Deployment:
    return _Deployment(
        store_root=tmp_path,
        runs=queue,
        console=accounts_with_alice(),
        keys=Ring(),
        parsers=paddle_registry(CountingPost()),
    )


@pytest.fixture
def client(deployment: _Deployment) -> TestClient:
    return TestClient(build_app(deployment))


def _ocr_run(queue: InMemoryRunQueue, deployment: _Deployment, *, tenant: str) -> Run:
    """A succeeded OCR run whose page real PaddleX would have turned (raw 270)."""
    store, blobs = deployment.stores_for(tenant)
    queue.submit(
        _OcrSpec(tenant, blobs.put(IMAGE)),  # type: ignore[arg-type]
        run_id=uuid4(),
        now=NOW,
        expires_at=NOW + timedelta(days=1),
    )
    run = queue.claim(
        worker_id="w", now=NOW, lease=timedelta(seconds=90), max_attempts=3, kind=RunKind.OCR
    )
    assert run is not None
    execute_one(
        run,
        queue=queue,
        blobs=blobs,
        store=store,
        registry=None,
        adapter=None,
        parsers=paddle_registry(_turned(270)),
        now=NOW,
    )
    finished = queue.get(run.run_id, tenant)
    assert finished is not None
    assert finished.processing_id is not None
    return finished


def test_console_result_carries_page_angles(
    client: TestClient, queue: InMemoryRunQueue, deployment: _Deployment
) -> None:
    cookie = sign_in(client)
    run = _ocr_run(queue, deployment, tenant="globex")

    body = client.get(f"/v1/console/runs/{run.run_id}/result", headers=cookie).json()

    assert body["page_angles"] == [90]
    assert "INV-777" in body["text"]


def test_console_result_without_a_sidecar_reports_null(
    client: TestClient, queue: InMemoryRunQueue, deployment: _Deployment
) -> None:
    cookie = sign_in(client)
    run = _ocr_run(queue, deployment, tenant="globex")
    store, _ = deployment.stores_for("globex")
    assert store.delete(orientation_id_for(run.processing_id))  # type: ignore[arg-type]

    response = client.get(f"/v1/console/runs/{run.run_id}/result", headers=cookie)

    assert response.status_code == 200
    assert response.json()["page_angles"] is None


def test_tenant_result_never_carries_page_angles(
    client: TestClient, queue: InMemoryRunQueue, deployment: _Deployment
) -> None:
    run = _ocr_run(queue, deployment, tenant="acme")

    response = client.get(
        f"/v1/runs/{run.run_id}/result", headers={"Authorization": f"Bearer {PLAIN_KEY}"}
    )

    assert response.status_code == 200
    assert "page_angles" not in response.json()
    assert "INV-777" in response.json()["text"]
