"""`GET /v1/console/overview`: what is moving, and what just failed.

Console monitoring plan (``docs/superpowers/plans/2026-10-20-console-monitoring.md``),
Task 2. Counts by kind and status -- `queued` and `running` at any age, the
three terminal statuses only within the last hour -- plus the ten newest
failures and the ten longest-running runs, each a `ConsoleRunSummary`.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue
from tests.support.console import ADMIN_KEY, Ring, accounts_with_alice, sign_in

from docdoc.api.app import _Deployment, build_app
from docdoc.runs import identity
from docdoc.runs.model import Run, RunKind, RunStatus

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)


@pytest.fixture
def queue() -> InMemoryRunQueue:
    return InMemoryRunQueue()


@pytest.fixture
def signed_in(
    tmp_path: Path, queue: InMemoryRunQueue, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, dict[str, str]]:
    # Pinned before signing in, so the session opens and is resolved on one clock.
    monkeypatch.setattr(identity, "now", lambda: NOW)
    client = TestClient(
        build_app(
            _Deployment(store_root=tmp_path, runs=queue, console=accounts_with_alice(), keys=Ring())
        )
    )
    return client, sign_in(client)


def _seed(
    queue: InMemoryRunQueue,
    status: RunStatus,
    *,
    minutes_ago: float,
    kind: RunKind = RunKind.OCR,
    tenant: str = "acme",
    **fields: object,
) -> Run:
    at = NOW - timedelta(minutes=minutes_ago)
    ocr = kind is RunKind.OCR
    run = Run(
        run_id=uuid4(),
        tenant_id=tenant,
        blob_id="sha256:" + "c" * 64,
        schema_identity=None if ocr else "invoice@1",
        ocr_parser="paddle" if ocr else None,
        kind=kind,
        status=status,
        processing_id="sha256:" + "d" * 64 if status is RunStatus.SUCCEEDED else None,
        created_at=at,
        updated_at=at,
        expires_at=NOW + timedelta(days=30),
        **fields,
    )
    queue._runs[run.run_id] = run
    return run


def _overview(signed_in: tuple[TestClient, dict[str, str]]) -> dict:
    client, cookie = signed_in
    response = client.get("/v1/console/overview", headers=cookie)
    assert response.status_code == 200
    return response.json()


def test_overview_of_an_empty_deployment(signed_in: tuple[TestClient, dict[str, str]]) -> None:
    assert _overview(signed_in) == {
        "window_minutes": 60,
        "counts": [],
        "recent_failures": [],
        "running": [],
    }


def test_overview_counts_active_always_and_terminal_within_the_hour(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    _seed(queue, RunStatus.QUEUED, minutes_ago=1)
    _seed(queue, RunStatus.QUEUED, minutes_ago=180)
    _seed(queue, RunStatus.RUNNING, minutes_ago=2, kind=RunKind.EXTRACTION)
    _seed(queue, RunStatus.FAILED, minutes_ago=10)
    _seed(queue, RunStatus.FAILED, minutes_ago=120)
    _seed(queue, RunStatus.SUCCEEDED, minutes_ago=5)
    # Exactly on the window's edge counts: "at or after now - 60 min".
    _seed(queue, RunStatus.CANCELLED, minutes_ago=60)

    body = _overview(signed_in)
    counts = {(c["kind"], c["status"]): c["count"] for c in body["counts"]}

    assert counts == {
        ("ocr", "queued"): 2,
        ("extraction", "running"): 1,
        ("ocr", "failed"): 1,
        ("ocr", "succeeded"): 1,
        ("ocr", "cancelled"): 1,
    }
    assert len(body["counts"]) == len(counts)


def test_overview_lists_are_ordered_and_capped_at_ten(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    failed = [_seed(queue, RunStatus.FAILED, minutes_ago=m) for m in range(12)]
    # Started longest ago first; every heartbeat is fresh, which must not matter.
    running = [
        _seed(queue, RunStatus.RUNNING, minutes_ago=0, started_at=NOW - timedelta(minutes=m))
        for m in range(12, 0, -1)
    ]
    # Newer than every listed row, but in neither list's status.
    _seed(queue, RunStatus.SUCCEEDED, minutes_ago=0)

    body = _overview(signed_in)

    assert [r["run_id"] for r in body["recent_failures"]] == [str(r.run_id) for r in failed[:10]]
    assert [r["run_id"] for r in body["running"]] == [str(r.run_id) for r in running[:10]]


def test_a_stuck_run_heads_running_now_however_fresh_its_heartbeat(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    """`updated_at` moves with every heartbeat; `started_at` does not."""
    fresh = _seed(queue, RunStatus.RUNNING, minutes_ago=0, started_at=NOW - timedelta(minutes=5))
    unstarted = _seed(queue, RunStatus.RUNNING, minutes_ago=30)
    stuck = _seed(queue, RunStatus.RUNNING, minutes_ago=1, started_at=NOW - timedelta(hours=3))

    body = _overview(signed_in)

    assert [r["run_id"] for r in body["running"]] == [
        str(stuck.run_id),
        str(fresh.run_id),
        str(unstarted.run_id),
    ]


def test_overview_rows_carry_the_new_fields(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    started = NOW - timedelta(minutes=3)
    _seed(
        queue,
        RunStatus.FAILED,
        minutes_ago=1,
        tenant="globex",
        started_at=started,
        failed_stage="parse",
        error_class="ParserError",
        error_reason="empty_result",
    )
    _seed(queue, RunStatus.RUNNING, minutes_ago=2, started_at=started)

    body = _overview(signed_in)
    failure = body["recent_failures"][0]
    running = body["running"][0]

    assert failure["tenant_id"] == "globex"
    assert failure["started_at"] == started.isoformat()
    assert failure["error_class"] == "ParserError"
    assert failure["error_reason"] == "empty_result"
    assert failure["ocr_parser"] == "paddle"
    assert running["error_class"] is None
    assert running["error_reason"] is None
    assert running["started_at"] == started.isoformat()


def test_overview_refuses_a_bearer_key(signed_in: tuple[TestClient, dict[str, str]]) -> None:
    client, _ = signed_in
    client.cookies.clear()
    response = client.get("/v1/console/overview", headers={"Authorization": f"Bearer {ADMIN_KEY}"})
    assert response.status_code == 401
    assert response.json()["error"]["class"] == "ConsoleSessionRequired"


def test_without_a_run_database_the_overview_is_503(tmp_path: Path) -> None:
    client = TestClient(
        build_app(_Deployment(store_root=tmp_path, console=accounts_with_alice(), keys=Ring()))
    )
    response = client.get("/v1/console/overview", headers=sign_in(client))
    assert response.status_code == 503
    assert response.json()["error"]["class"] == "RunStateUnavailableError"
