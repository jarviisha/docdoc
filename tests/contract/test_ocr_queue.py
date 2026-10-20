"""`POST /v1/ocr` queues, bounded twice; `GET /v1/runs/{id}/result` serves it (014).

ADR-0022. The route accepts a document into the run queue and returns before any
OCR happens. A consumer over its own cap is told 429; a deployment over its
global cap is told 503, "overloaded, send again later". Either refusal leaves no
run and no blob behind.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue
from tests.support.ocr import CountingPost, paddle_registry

from docdoc.api.app import _Deployment, _load_ceilings, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.runs.errors import RunStateUnavailableError
from docdoc.runs.model import RunKind, RunOutcome, RunStatus
from docdoc.runs.principal import digest_of
from docdoc.runs.worker import execute_one

IMAGE = (Path(__file__).parent.parent / "fixtures" / "image" / "sample_page.png").read_bytes()
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _no_keys_or_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCDOC_API_KEYS_FILE", raising=False)
    monkeypatch.delenv("DOCDOC_LIMITS_FILE", raising=False)
    _load_ceilings()


def _deployment(tmp_path: Path, post: CountingPost, **fields: Any) -> _Deployment:
    return _Deployment(
        store_root=tmp_path,
        parsers=paddle_registry(post),
        runs=fields.pop("runs", InMemoryRunQueue()),
        ocr_queue_per_tenant=fields.pop("per_tenant", 2),
        ocr_queue_total=fields.pop("total", 3),
        **fields,
    )


def _client(deployment: _Deployment) -> TestClient:
    return TestClient(build_app(deployment))


def _ocr(client: TestClient, key: str | None = None, **params: Any) -> Any:
    headers = {"Authorization": f"Bearer {key}"} if key else {}
    return client.post(
        "/v1/ocr", params={"parser": "paddle", **params}, content=IMAGE, headers=headers
    )


def _blob_count(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file() and "blobs" in path.parts)


def _with_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, limits: dict | None = None) -> None:
    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps(
            {
                "keys": [
                    {"sha256": digest_of("a-key"), "tenant_id": "a"},
                    {"sha256": digest_of("b-key"), "tenant_id": "b"},
                    {"sha256": digest_of("bulk-key"), "tenant_id": "bulk"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))
    if limits is not None:
        path = tmp_path / "limits.json"
        path.write_text(json.dumps(limits), encoding="utf-8")
        monkeypatch.setenv("DOCDOC_LIMITS_FILE", str(path))


def test_submit_returns_202_without_ocr(tmp_path: Path) -> None:
    post = CountingPost()
    queue = InMemoryRunQueue()
    response = _ocr(_client(_deployment(tmp_path, post, runs=queue)), language="vie")

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    assert body["kind"] == "ocr"
    assert post.calls == 0
    assert queue.count_active(kind=RunKind.OCR) == 1
    assert _blob_count(tmp_path) == 1


def test_tenant_cap_429(tmp_path: Path) -> None:
    client = _client(_deployment(tmp_path, CountingPost(), total=10))
    assert [_ocr(client).status_code for _ in range(2)] == [202, 202]

    refused = _ocr(client)

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "30"
    assert refused.json()["limit"] == "ocr_queue"
    assert refused.json()["allowed"] == 2


def test_global_cap_503(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_keys(tmp_path, monkeypatch)
    client = _client(_deployment(tmp_path, CountingPost(), per_tenant=2, total=3))
    assert [_ocr(client, k).status_code for k in ("a-key", "a-key", "b-key")] == [202] * 3

    refused = _ocr(client, "b-key")

    assert refused.status_code == 503
    assert refused.headers["Retry-After"] == "30"
    assert refused.json()["error"] == "system_overloaded"
    assert "overloaded" in refused.json()["message"]


def test_one_tenant_full_does_not_refuse_another(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_keys(tmp_path, monkeypatch)
    client = _client(_deployment(tmp_path, CountingPost(), per_tenant=2, total=10))
    assert [_ocr(client, "a-key").status_code for _ in range(3)] == [202, 202, 429]
    assert _ocr(client, "b-key").status_code == 202


def test_refusals_store_nothing(tmp_path: Path) -> None:
    queue = InMemoryRunQueue()
    small = _client(_deployment(tmp_path, CountingPost(), runs=queue, max_request_bytes=100))
    assert small.post("/v1/ocr", content=IMAGE).status_code == 413

    client = _client(_deployment(tmp_path, CountingPost(), runs=queue, per_tenant=1))
    assert _ocr(client, parser="nope").status_code == 422
    assert _ocr(client, language="klingon").status_code == 422
    assert queue.count_active(kind=RunKind.OCR) == 0
    assert _blob_count(tmp_path) == 0

    assert _ocr(client).status_code == 202
    assert _ocr(client).status_code == 429
    assert queue.count_active(kind=RunKind.OCR) == 1
    assert _blob_count(tmp_path) == 1


class _CountlessQueue(InMemoryRunQueue):
    def count_active(self, **_: Any) -> int:
        raise RunStateUnavailableError("database unreachable")


def test_count_unavailable_is_transient_503(tmp_path: Path) -> None:
    response = _ocr(_client(_deployment(tmp_path, CountingPost(), runs=_CountlessQueue())))
    assert response.status_code == 503
    assert "Retry-After" in response.headers
    assert _blob_count(tmp_path) == 0


def test_no_database_503(tmp_path: Path) -> None:
    deployment = _Deployment(store_root=tmp_path, parsers=paddle_registry(CountingPost()))
    response = _ocr(_client(deployment))
    assert response.status_code == 503
    assert "Retry-After" not in response.headers


def test_finished_slot_frees_cap(tmp_path: Path) -> None:
    queue = InMemoryRunQueue()
    client = _client(_deployment(tmp_path, CountingPost(), runs=queue, per_tenant=1))
    first = _ocr(client).json()
    assert _ocr(client).status_code == 429

    from uuid import UUID

    queue.finish(UUID(first["run_id"]), RunOutcome(status=RunStatus.CANCELLED), now=NOW)
    assert _ocr(client).status_code == 202


def _work(queue: InMemoryRunQueue, tmp_path: Path, post: CountingPost) -> None:
    run = queue.claim(
        worker_id="w",
        now=NOW,
        lease=__import__("datetime").timedelta(seconds=90),
        max_attempts=3,
        kind=RunKind.OCR,
    )
    execute_one(
        run,
        queue=queue,
        blobs=BlobStore(tmp_path),
        store=FileArtifactStore(tmp_path),
        registry=None,
        adapter=None,
        parsers=paddle_registry(post),
        now=NOW,
    )


def test_result_route(tmp_path: Path) -> None:
    post = CountingPost("INV-777")
    queue = InMemoryRunQueue()
    client = _client(_deployment(tmp_path, post, runs=queue))
    run_id = _ocr(client).json()["run_id"]

    early = client.get(f"/v1/runs/{run_id}/result")
    assert early.status_code == 409
    assert early.json()["error"] == "run_not_succeeded"

    _work(queue, tmp_path, post)

    state = client.get(f"/v1/runs/{run_id}")
    assert state.json()["status"] == "succeeded"
    result = client.get(f"/v1/runs/{run_id}/result")
    assert result.status_code == 200
    assert "INV-777" in result.json()["text"]


def test_result_route_refuses_an_extraction_run(tmp_path: Path) -> None:
    queue = InMemoryRunQueue()

    class _Spec:
        tenant_id = "default"
        blob_id = "sha256:" + "b" * 64
        schema_identity = "invoice@1"
        request_id = None
        idempotency_key = None

    from uuid import uuid4

    run = queue.submit(_Spec(), run_id=uuid4(), now=NOW, expires_at=NOW)  # type: ignore[arg-type]
    client = _client(_deployment(tmp_path, CountingPost(), runs=queue))
    response = client.get(f"/v1/runs/{run.run_id}/result")
    assert response.status_code == 409
    assert response.json()["error"] == "not_an_ocr_run"


def test_result_route_is_tenant_scoped(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_keys(tmp_path, monkeypatch)
    client = _client(_deployment(tmp_path, CountingPost(), total=10))
    run_id = _ocr(client, "a-key").json()["run_id"]
    other = client.get(f"/v1/runs/{run_id}/result", headers={"Authorization": "Bearer b-key"})
    assert other.status_code == 404
    assert other.json()["error"]["class"] == "RunNotFoundError"


def test_limits_file_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_keys(tmp_path, monkeypatch, limits={"tenants": {"bulk": {"ocr_queue": 5}}})
    client = _client(_deployment(tmp_path, CountingPost(), per_tenant=2, total=100))
    assert [_ocr(client, "bulk-key").status_code for _ in range(6)] == [202] * 5 + [429]
    assert [_ocr(client, "a-key").status_code for _ in range(3)] == [202, 202, 429]


# -- final review fixes --------------------------------------------------------


def test_run_listing_includes_ocr_runs(tmp_path: Path) -> None:
    """Review finding 1: a page with an OCR run on it is a page, not a 500."""
    client = _client(_deployment(tmp_path, CountingPost()))
    run_id = _ocr(client).json()["run_id"]

    listing = client.get("/v1/runs")

    assert listing.status_code == 200
    (entry,) = listing.json()["runs"]
    assert entry["run_id"] == run_id
    assert entry["kind"] == "ocr"
    assert entry["schema_identity"] is None


def test_an_ocr_processing_id_is_not_a_job(tmp_path: Path) -> None:
    """Review finding 2: the jobs routes answer about validation results only."""
    post = CountingPost()
    queue = InMemoryRunQueue()
    client = _client(_deployment(tmp_path, post, runs=queue))
    run_id = _ocr(client).json()["run_id"]
    _work(queue, tmp_path, post)
    processing_id = client.get(f"/v1/runs/{run_id}").json()["processing_id"]

    assert client.get(f"/v1/jobs/{processing_id}").json()["status"] == "unavailable"
    result = client.get(f"/v1/jobs/{processing_id}/result")
    assert result.status_code != 500


def test_a_routing_policy_does_not_break_reading_an_ocr_run(tmp_path: Path) -> None:
    """Review finding 2: routing decides about validation results, and an OCR run has none."""
    from docdoc.runs.routing import RoutingPolicy

    post = CountingPost()
    queue = InMemoryRunQueue()
    policy = RoutingPolicy(version="default@1", required_fields=frozenset({"total"}))
    client = _client(_deployment(tmp_path, post, runs=queue, routing=policy))
    run_id = _ocr(client).json()["run_id"]
    _work(queue, tmp_path, post)

    state = client.get(f"/v1/runs/{run_id}")

    assert state.status_code == 200
    assert state.json()["status"] == "succeeded"
    assert "routing" not in state.json()


def test_limits_file_default_ocr_queue(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Review finding 5: `default.ocr_queue` caps every tenant without its own entry."""
    _with_keys(
        tmp_path,
        monkeypatch,
        limits={"default": {"ocr_queue": 1}, "tenants": {"bulk": {"ocr_queue": 3}}},
    )
    client = _client(_deployment(tmp_path, CountingPost(), per_tenant=50, total=100))
    assert [_ocr(client, "a-key").status_code for _ in range(2)] == [202, 429]
    assert [_ocr(client, "bulk-key").status_code for _ in range(4)] == [202] * 3 + [429]


def test_limits_file_refuses_a_malformed_ocr_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review finding 5: a cap that is not a positive integer is refused, not ignored."""
    _with_keys(tmp_path, monkeypatch, limits={"tenants": {"bulk": {"ocr_queue": "50"}}})
    with pytest.raises(ValueError, match="ocr_queue"):
        _load_ceilings()


class _LoopSpyQueue(InMemoryRunQueue):
    """Records whether the cap count ran on the event loop's thread."""

    def __init__(self) -> None:
        super().__init__()
        self.on_loop: list[bool] = []

    def count_active(self, **kwargs: Any) -> int:
        import asyncio

        try:
            asyncio.get_running_loop()
            self.on_loop.append(True)
        except RuntimeError:
            self.on_loop.append(False)
        return super().count_active(**kwargs)


def test_submission_does_its_blocking_work_off_the_event_loop(tmp_path: Path) -> None:
    """Review finding 3: parse planning, the counts, and the S3 put are blocking."""
    queue = _LoopSpyQueue()
    assert _ocr(_client(_deployment(tmp_path, CountingPost(), runs=queue))).status_code == 202
    assert queue.on_loop
    assert not any(queue.on_loop)
