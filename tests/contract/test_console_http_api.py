"""T037 — the shape of `GET /v1/runs`, and the four ways it says no.

The listing is the one new read Milestone 11 adds. What it returns is checked
here; what it must never return to *another tenant* is `test_run_listing.py`,
because that needs two tenants and a shared store and is an integration concern.

Two assertions here are about absence rather than shape, and they are the ones
worth reading: a `422` on an oversized limit instead of a silent clamp, and one
body for every kind of unusable cursor.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.api.app import DEFAULT_RUN_PAGE, MAX_RUN_PAGE, _Deployment, build_app
from docdoc.api.paging import Cursor, encode
from docdoc.runs.model import Run, RunStatus
from docdoc.runs.principal import digest_of

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
KEY = "acme-key"
OTHER_KEY = "globex-key"


@pytest.fixture
def queue() -> InMemoryRunQueue:
    return InMemoryRunQueue()


@pytest.fixture
def client(tmp_path: Path, queue: InMemoryRunQueue, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps(
            {
                "keys": [
                    {"sha256": digest_of(KEY), "tenant_id": "acme"},
                    {"sha256": digest_of(OTHER_KEY), "tenant_id": "globex"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))
    return TestClient(build_app(_Deployment(runs=queue)))


def _run(
    queue: InMemoryRunQueue,
    *,
    tenant: str = "acme",
    status: RunStatus = RunStatus.SUCCEEDED,
    age: int = 0,
) -> Run:
    run = Run(
        run_id=uuid4(),
        tenant_id=tenant,
        blob_id="sha256:" + "a" * 64,
        schema_identity="invoice@1",
        status=status,
        processing_id=("sha256:" + "b" * 64) if status is RunStatus.SUCCEEDED else None,
        created_at=NOW - timedelta(minutes=age),
        updated_at=NOW - timedelta(minutes=age),
        expires_at=NOW + timedelta(days=30),
    )
    queue._runs[run.run_id] = run
    return run


def _list(client: TestClient, key: str = KEY, **params: object) -> object:
    return client.get("/v1/runs", params=params, headers={"Authorization": f"Bearer {key}"})


# -- shape --------------------------------------------------------------------


def test_a_run_carries_the_six_fields_a_list_may_show(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    run = _run(queue)

    body = _list(client).json()

    assert body["runs"] == [
        {
            "run_id": str(run.run_id),
            "status": "succeeded",
            "created_at": run.created_at.isoformat(),
            "finished_at": run.updated_at.isoformat(),
            "blob_id": run.blob_id,
            "schema_identity": "invoice@1",
            "processing_id": run.processing_id,
        }
    ]


def test_no_row_carries_content(client: TestClient, queue: InMemoryRunQueue) -> None:
    """FR-019. The absent fields are the reason this model exists."""
    _run(queue)

    row = _list(client).json()["runs"][0]

    for absent in ("stage_outcomes", "error_class", "failed_stage", "tenant_id", "tokens_used"):
        assert absent not in row


def test_a_running_run_has_no_finished_at(client: TestClient, queue: InMemoryRunQueue) -> None:
    _run(queue, status=RunStatus.RUNNING)

    row = _list(client).json()["runs"][0]

    assert row["finished_at"] is None
    assert row["processing_id"] is None


def test_newest_first(client: TestClient, queue: InMemoryRunQueue) -> None:
    old = _run(queue, age=10)
    new = _run(queue, age=0)

    body = _list(client).json()

    assert [row["run_id"] for row in body["runs"]] == [str(new.run_id), str(old.run_id)]


def test_an_empty_tenant_gets_an_empty_list_and_no_cursor(client: TestClient) -> None:
    body = _list(client).json()

    assert body == {"runs": [], "next_cursor": None}


# -- filtering ----------------------------------------------------------------


def test_a_status_filter_returns_exactly_that_status(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    failed = _run(queue, status=RunStatus.FAILED)
    _run(queue, status=RunStatus.SUCCEEDED)

    body = _list(client, status="failed").json()

    assert [row["run_id"] for row in body["runs"]] == [str(failed.run_id)]


def test_an_unknown_status_is_refused_rather_than_returning_nothing(client: TestClient) -> None:
    """A filter nobody can satisfy is a client bug, not an empty result."""
    response = _list(client, status="reviewed")

    assert response.status_code == 422
    assert response.json()["error"]["class"] == "InvalidRunStatus"


# -- paging -------------------------------------------------------------------


def test_the_page_size_defaults_and_is_bounded(client: TestClient, queue: InMemoryRunQueue) -> None:
    assert DEFAULT_RUN_PAGE == 50
    assert MAX_RUN_PAGE == 200

    for age in range(3):
        _run(queue, age=age)

    assert len(_list(client, limit=2).json()["runs"]) == 2


def test_an_oversized_limit_is_refused_and_not_clamped(client: TestClient) -> None:
    """A silently clamped page is one a client pages through wrongly forever."""
    response = _list(client, limit=MAX_RUN_PAGE + 1)

    assert response.status_code == 422
    assert response.json()["error"]["class"] == "InvalidPageSize"


def test_a_zero_limit_is_refused(client: TestClient) -> None:
    assert _list(client, limit=0).status_code == 422


def test_a_short_page_ends_and_a_full_one_offers_a_cursor(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """The boundary case costs one round trip, and it is the cheap side.

    A page filled exactly to the limit cannot know whether it is the last, and
    the alternative is a `COUNT(*)` on every page to answer a question that
    matters once per listing.
    """
    for age in range(3):
        _run(queue, age=age)

    assert _list(client, limit=4).json()["next_cursor"] is None, "a short page is the end"

    full = _list(client, limit=3).json()
    assert full["next_cursor"] is not None, "a full page cannot know it is the last"

    # And following it lands on the empty page, which does end. An empty list
    # with a cursor beside it is not a state this route produces.
    after = _list(client, limit=3, cursor=full["next_cursor"]).json()
    assert after == {"runs": [], "next_cursor": None}


def test_a_malformed_cursor_and_a_foreign_one_get_the_same_answer(
    client: TestClient,
) -> None:
    """FR-016. Two answers would say which foreign cursors are well formed."""
    foreign = encode(Cursor("globex", NOW, UUID(int=1)))

    malformed = _list(client, cursor="not-a-cursor")
    stolen = _list(client, cursor=foreign)

    assert malformed.status_code == 400
    assert stolen.status_code == 400
    assert malformed.text == stolen.text


# -- what the listing is not --------------------------------------------------


def test_a_deployment_without_runs_says_so_rather_than_returning_nothing(
    tmp_path: Path,
) -> None:
    """An empty list would read as "nothing has been submitted" (Edge Cases)."""
    response = TestClient(build_app(_Deployment(store_root=tmp_path))).get("/v1/runs")

    assert response.status_code == 503
    assert response.json()["error"]["class"] == "RunStateUnavailableError"


def test_the_listing_needs_no_administrative_scope(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """FR-021. A principal lists their own tenant's runs, scope or not."""
    _run(queue, tenant="globex")

    assert _list(client, key=OTHER_KEY).status_code == 200


def test_a_removed_run_is_absent_from_the_listing(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """FR-020. The listing is not a second way to learn something was deleted."""
    run = _run(queue)
    queue.entomb([run], now=NOW, policy="retention")

    assert _list(client).json()["runs"] == []
