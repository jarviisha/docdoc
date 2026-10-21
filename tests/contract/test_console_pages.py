"""`GET /v1/console/runs/{run_id}/pages/{page}`: an OCR run's page images (019).

PNG and JPEG are served as stored, page 0 only; a PDF page is rendered to PNG
with its longer edge capped at 2000 px. Extraction runs have no page images.
"""

from __future__ import annotations

import io
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue
from tests.support.console import ADMIN_KEY, Ring, accounts_with_alice, sign_in

from docdoc.api.app import _Deployment, build_app
from docdoc.runs.model import Run, RunKind, RunStatus

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=UTC)
IMAGE = (Path(__file__).parent.parent / "fixtures" / "image" / "sample_page.png").read_bytes()


@pytest.fixture
def queue() -> InMemoryRunQueue:
    return InMemoryRunQueue()


@pytest.fixture
def deployment(tmp_path: Path, queue: InMemoryRunQueue) -> _Deployment:
    return _Deployment(store_root=tmp_path, runs=queue, console=accounts_with_alice(), keys=Ring())


@pytest.fixture
def signed_in(deployment: _Deployment) -> tuple[TestClient, dict[str, str]]:
    client = TestClient(build_app(deployment))
    return client, sign_in(client)


def _run(queue: InMemoryRunQueue, blob_id: str, *, kind: RunKind = RunKind.OCR) -> Run:
    run = Run(
        run_id=uuid4(),
        tenant_id="globex",
        blob_id=blob_id,
        schema_identity=None if kind is RunKind.OCR else "invoice@1",
        status=RunStatus.SUCCEEDED,
        processing_id=None,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=1),
        kind=kind,
    )
    queue._runs[run.run_id] = run
    return run


def _stored(deployment: _Deployment, data: bytes) -> str:
    _, blobs = deployment.stores_for("globex")
    return str(blobs.put(data))


def _pdf(pages: int) -> bytes:
    pdfium = pytest.importorskip("pypdfium2")
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(612, 792)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def test_console_page_of_a_png_run(
    signed_in: tuple[TestClient, dict[str, str]],
    queue: InMemoryRunQueue,
    deployment: _Deployment,
) -> None:
    client, cookie = signed_in
    run = _run(queue, _stored(deployment, IMAGE))

    r = client.get(f"/v1/console/runs/{run.run_id}/pages/0", headers=cookie)

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "private, max-age=300"
    assert r.content == IMAGE
    assert client.get(f"/v1/console/runs/{run.run_id}/pages/1", headers=cookie).status_code == 404


def test_console_page_of_a_jpeg_run(
    signed_in: tuple[TestClient, dict[str, str]],
    queue: InMemoryRunQueue,
    deployment: _Deployment,
) -> None:
    client, cookie = signed_in
    jpeg = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00" + b"\x00" * 16
    run = _run(queue, _stored(deployment, jpeg))

    r = client.get(f"/v1/console/runs/{run.run_id}/pages/0", headers=cookie)

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "private, max-age=300"
    assert r.content == jpeg


def test_console_page_of_a_corrupt_pdf_is_404(
    signed_in: tuple[TestClient, dict[str, str]],
    queue: InMemoryRunQueue,
    deployment: _Deployment,
) -> None:
    pytest.importorskip("pypdfium2")
    client, cookie = signed_in
    run = _run(queue, _stored(deployment, b"%PDF-1.4 not really"))

    r = client.get(f"/v1/console/runs/{run.run_id}/pages/0", headers=cookie)

    assert r.status_code == 404
    assert r.json()["error"]["class"] == "RunNotFoundError"


def test_console_page_of_a_pdf_run(
    signed_in: tuple[TestClient, dict[str, str]],
    queue: InMemoryRunQueue,
    deployment: _Deployment,
) -> None:
    client, cookie = signed_in
    run = _run(queue, _stored(deployment, _pdf(2)))

    r = client.get(f"/v1/console/runs/{run.run_id}/pages/0", headers=cookie)
    missing = client.get(f"/v1/console/runs/{run.run_id}/pages/5", headers=cookie)

    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    assert r.headers["cache-control"] == "private, max-age=300"
    assert r.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert missing.status_code == 404
    # The one 404 an unknown run gets, so a page count is no existence oracle.
    assert (
        missing.json() == client.get(f"/v1/console/runs/{uuid4()}/pages/0", headers=cookie).json()
    )


def test_console_page_of_a_pdf_without_pypdfium2_is_503(
    signed_in: tuple[TestClient, dict[str, str]],
    queue: InMemoryRunQueue,
    deployment: _Deployment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, cookie = signed_in
    run = _run(queue, _stored(deployment, b"%PDF-1.4 not really"))
    monkeypatch.setitem(sys.modules, "pypdfium2", None)

    r = client.get(f"/v1/console/runs/{run.run_id}/pages/0", headers=cookie)

    assert r.status_code == 503
    assert r.json() == {
        "error": {
            "class": "PageRenderingUnavailable",
            "message": "rendering PDF pages needs the docdoc[ocr] extra (pypdfium2)",
        }
    }


def test_console_page_of_an_extraction_run_is_409(
    signed_in: tuple[TestClient, dict[str, str]],
    queue: InMemoryRunQueue,
    deployment: _Deployment,
) -> None:
    client, cookie = signed_in
    run = _run(queue, _stored(deployment, IMAGE), kind=RunKind.EXTRACTION)

    r = client.get(f"/v1/console/runs/{run.run_id}/pages/0", headers=cookie)

    assert r.status_code == 409
    assert r.json()["error"] == "no_page_images"
    assert r.json()["detail"]


def test_console_page_of_an_unknown_run_or_missing_blob_is_404(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in
    run = _run(queue, "sha256:" + "c" * 64)

    for path in (
        f"/v1/console/runs/{run.run_id}/pages/0",
        f"/v1/console/runs/{uuid4()}/pages/0",
        "/v1/console/runs/not-a-uuid/pages/0",
    ):
        assert client.get(path, headers=cookie).status_code == 404, path


def test_console_page_of_an_erased_run_is_410(
    signed_in: tuple[TestClient, dict[str, str]],
    queue: InMemoryRunQueue,
    deployment: _Deployment,
) -> None:
    client, cookie = signed_in
    run = _run(queue, _stored(deployment, IMAGE))
    queue.entomb([run], now=NOW, policy="retention")

    r = client.get(f"/v1/console/runs/{run.run_id}/pages/0", headers=cookie)

    assert r.status_code == 410
    # The same tombstone the sibling routes answer with.
    assert r.json() == client.get(f"/v1/console/runs/{run.run_id}", headers=cookie).json()
    assert r.json()["error"] == "run_erased"


def test_console_page_refuses_a_bearer_key(
    signed_in: tuple[TestClient, dict[str, str]],
    queue: InMemoryRunQueue,
    deployment: _Deployment,
) -> None:
    client, _ = signed_in
    client.cookies.clear()
    run = _run(queue, _stored(deployment, IMAGE))

    r = client.get(
        f"/v1/console/runs/{run.run_id}/pages/0",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
    )

    assert r.status_code == 401
    assert r.json()["error"]["class"] == "ConsoleSessionRequired"
