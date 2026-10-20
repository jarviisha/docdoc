"""The console reads runs across tenants (ADR-0023 §3, §4).

`/v1/console/runs*` mirrors `/v1/runs*` with the tenant predicate lifted: a
signed-in operator sees every tenant's runs, and each one names its tenant.
Everything else -- validation, the tombstone answer, the one answer for unknown
and malformed -- is the tenant route's, unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue
from tests.support.console import ADMIN_KEY, Ring, accounts_with_alice, sign_in
from tests.support.ocr import CountingPost, paddle_registry

from docdoc.api.app import _Deployment, build_app
from docdoc.runs.model import Run, RunKind, RunStatus
from docdoc.runs.worker import execute_one

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
IMAGE = (Path(__file__).parent.parent / "fixtures" / "image" / "sample_page.png").read_bytes()


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
        parsers=paddle_registry(CountingPost("INV-777")),
    )


@pytest.fixture
def signed_in(deployment: _Deployment) -> tuple[TestClient, dict[str, str]]:
    client = TestClient(build_app(deployment))
    return client, sign_in(client)


def _seed(queue: InMemoryRunQueue, tenant: str, *, age: int, **fields: object) -> Run:
    run = Run(
        run_id=uuid4(),
        tenant_id=tenant,
        blob_id="sha256:" + "c" * 64,
        schema_identity="invoice@1",
        status=fields.pop("status", RunStatus.SUCCEEDED),
        processing_id=fields.pop("processing_id", "sha256:" + "d" * 64),
        created_at=NOW - timedelta(minutes=age),
        updated_at=NOW - timedelta(minutes=age),
        expires_at=NOW + timedelta(days=30),
        **fields,
    )
    queue._runs[run.run_id] = run
    return run


def test_the_list_spans_tenants_and_names_each_rows_tenant(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in
    acme = _seed(queue, "acme", age=1)
    globex = _seed(queue, "globex", age=0)

    body = client.get("/v1/console/runs", headers=cookie).json()

    assert [row["run_id"] for row in body["runs"]] == [str(globex.run_id), str(acme.run_id)]
    assert [row["tenant_id"] for row in body["runs"]] == ["globex", "acme"]
    assert body["next_cursor"] is None


def test_a_tenant_filter_returns_only_that_tenant(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in
    acme = _seed(queue, "acme", age=1)
    _seed(queue, "globex", age=0)

    body = client.get("/v1/console/runs", params={"tenant": "acme"}, headers=cookie).json()

    assert [row["run_id"] for row in body["runs"]] == [str(acme.run_id)]


def test_a_status_and_a_limit_are_validated_as_on_the_tenant_route(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, cookie = signed_in
    bad_status = client.get("/v1/console/runs", params={"status": "nope"}, headers=cookie)
    bad_limit = client.get("/v1/console/runs", params={"limit": 0}, headers=cookie)

    assert bad_status.status_code == 422
    assert bad_status.json()["error"]["class"] == "InvalidRunStatus"
    assert bad_limit.status_code == 422
    assert bad_limit.json()["error"]["class"] == "InvalidPageSize"


def test_a_cursor_is_bound_to_its_tenant_filter(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in
    for age in range(2):
        _seed(queue, "acme", age=age)
        _seed(queue, "globex", age=age)

    first = client.get(
        "/v1/console/runs", params={"tenant": "acme", "limit": 1}, headers=cookie
    ).json()
    cursor = first["next_cursor"]
    assert cursor is not None

    same = client.get(
        "/v1/console/runs", params={"tenant": "acme", "limit": 1, "cursor": cursor}, headers=cookie
    )
    assert same.status_code == 200
    for params in ({"cursor": cursor}, {"tenant": "globex", "cursor": cursor}):
        response = client.get("/v1/console/runs", params=params, headers=cookie)
        assert response.status_code == 400
        assert response.json()["error"]["class"] == "InvalidCursor"


def test_detail_finds_a_run_without_knowing_its_tenant(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in
    globex = _seed(queue, "globex", age=0)

    response = client.get(f"/v1/console/runs/{globex.run_id}", headers=cookie)

    assert response.status_code == 200
    assert response.json()["tenant_id"] == "globex"
    assert response.json()["run_id"] == str(globex.run_id)


def test_detail_of_a_removed_run_is_410(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in
    removed = _seed(queue, "globex", age=0)
    queue.entomb([removed], now=NOW, policy="retention")

    response = client.get(f"/v1/console/runs/{removed.run_id}", headers=cookie)

    assert response.status_code == 410
    assert response.json() == {
        "error": "run_erased",
        "run_id": str(removed.run_id),
        "deleted_at": NOW.isoformat(),
        "policy": "retention",
    }


def test_an_unknown_and_a_malformed_id_get_one_answer(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, cookie = signed_in
    answers = [
        client.get(f"/v1/console/runs/{run_id}{suffix}", headers=cookie)
        for run_id in (uuid4(), "not-a-uuid")
        for suffix in ("", "/delivery", "/result")
    ]

    assert {response.status_code for response in answers} == {404}
    assert len({response.text for response in answers}) == 1


def test_result_of_an_unfinished_run_is_409(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in
    run = _seed(queue, "globex", age=0, status=RunStatus.QUEUED, processing_id=None)

    response = client.get(f"/v1/console/runs/{run.run_id}/result", headers=cookie)

    assert response.status_code == 409
    assert response.json() == {"error": "run_not_succeeded", "status": "queued"}


def test_result_of_a_succeeded_extraction_run_without_its_content_is_410(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    """The extraction branch reads the owner's store; nothing there is gone."""
    client, cookie = signed_in
    run = _seed(queue, "globex", age=0)

    response = client.get(f"/v1/console/runs/{run.run_id}/result", headers=cookie)

    assert response.status_code == 410
    assert response.json() == {"error": "result_expired"}


@dataclass
class _OcrSpec:
    tenant_id: str
    blob_id: str
    schema_identity: None = None
    request_id: None = None
    idempotency_key: None = None
    kind: str = "ocr"
    ocr_parser: str = "paddle"
    ocr_language: None = None


def test_result_of_a_succeeded_ocr_run_is_its_document(
    signed_in: tuple[TestClient, dict[str, str]],
    queue: InMemoryRunQueue,
    deployment: _Deployment,
) -> None:
    client, cookie = signed_in
    store, blobs = deployment.stores_for("globex")
    queue.submit(
        _OcrSpec(tenant_id="globex", blob_id=blobs.put(IMAGE)),  # type: ignore[arg-type]
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
        parsers=paddle_registry(CountingPost("INV-777")),
        now=NOW,
    )

    response = client.get(f"/v1/console/runs/{run.run_id}/result", headers=cookie)

    assert response.status_code == 200
    assert "pages" in response.json()
    assert "INV-777" in response.json()["text"]


def test_the_console_run_routes_refuse_a_bearer_key(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, _ = signed_in
    client.cookies.clear()
    run = _seed(queue, "acme", age=0)
    bearer = {"Authorization": f"Bearer {ADMIN_KEY}"}

    for path in (
        "/v1/console/runs",
        f"/v1/console/runs/{run.run_id}",
        f"/v1/console/runs/{run.run_id}/delivery",
        f"/v1/console/runs/{run.run_id}/result",
    ):
        response = client.get(path, headers=bearer)
        assert response.status_code == 401, path
        assert response.json()["error"]["class"] == "ConsoleSessionRequired"


def test_without_a_run_database_the_console_run_routes_are_503(tmp_path: Path) -> None:
    """The same answer `GET /v1/runs` gives a deployment that records no runs."""
    client = TestClient(
        build_app(_Deployment(store_root=tmp_path, console=accounts_with_alice(), keys=Ring()))
    )
    cookie = sign_in(client)
    run_id = uuid4()
    for path in (
        "/v1/console/runs",
        f"/v1/console/runs/{run_id}",
        f"/v1/console/runs/{run_id}/delivery",
        f"/v1/console/runs/{run_id}/result",
    ):
        response = client.get(path, headers=cookie)
        assert response.status_code == 503, path
        assert response.json()["error"]["class"] == "RunStateUnavailableError"
