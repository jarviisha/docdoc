"""T038 — paging is exact, and one tenant sees nothing of another.

Three claims, and each fails in a different way:

- **SC-007**: tenant A sees none of tenant B's runs, through the listing, a
  cursor, or a filter. The two tenants hold runs over byte-identical documents,
  because that is the arrangement where a leak is derivable rather than guessed.
- **SC-009**: paging an unchanging set returns every run exactly once. The
  interesting half is the one an `OFFSET` page fails: a run submitted between two
  pages must not cause a repeat.
- **FR-020**: a removed run is absent from the listing and still reachable by
  identity to its owner.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.api.app import _Deployment, build_app
from docdoc.runs.model import Run, RunStatus
from docdoc.runs.principal import digest_of

NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
ACME = "acme-key"
GLOBEX = "globex-key"

#: The same document under both tenants. Content addressing means tenant B can
#: *derive* this identity by submitting the same invoice — so isolation cannot
#: rest on the identifier being secret.
SHARED_BLOB = "sha256:" + "c" * 64


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
                    {"sha256": digest_of(ACME), "tenant_id": "acme"},
                    {"sha256": digest_of(GLOBEX), "tenant_id": "globex"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))
    return TestClient(build_app(_Deployment(runs=queue)))


def _submit(queue: InMemoryRunQueue, tenant: str, *, age: int) -> Run:
    run = Run(
        run_id=uuid4(),
        tenant_id=tenant,
        blob_id=SHARED_BLOB,
        schema_identity="invoice@1",
        status=RunStatus.SUCCEEDED,
        processing_id="sha256:" + "d" * 64,
        created_at=NOW - timedelta(minutes=age),
        updated_at=NOW - timedelta(minutes=age),
        expires_at=NOW + timedelta(days=30),
    )
    queue._runs[run.run_id] = run
    return run


def _walk(client: TestClient, key: str, *, limit: int = 2) -> list[str]:
    """Every run reachable by paging, in order, following cursors to the end."""
    seen: list[str] = []
    cursor: str | None = None
    for _ in range(50):  # a bound, so a cursor loop fails as a test rather than hangs
        params: dict[str, object] = {"limit": limit}
        if cursor is not None:
            params["cursor"] = cursor
        body = client.get(
            "/v1/runs", params=params, headers={"Authorization": f"Bearer {key}"}
        ).json()
        seen.extend(row["run_id"] for row in body["runs"])
        cursor = body["next_cursor"]
        if cursor is None:
            return seen
    raise AssertionError("the cursor never ended")


# -- isolation ----------------------------------------------------------------


def test_one_tenants_listing_holds_none_of_the_others(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """SC-007, over byte-identical documents."""
    mine = [_submit(queue, "acme", age=age) for age in range(3)]
    theirs = [_submit(queue, "globex", age=age) for age in range(3)]

    listed = set(_walk(client, ACME))

    assert listed == {str(run.run_id) for run in mine}
    assert listed.isdisjoint({str(run.run_id) for run in theirs})


def test_a_filter_cannot_reach_across_tenants(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    _submit(queue, "globex", age=0)

    body = client.get(
        "/v1/runs", params={"status": "succeeded"}, headers={"Authorization": f"Bearer {ACME}"}
    ).json()

    assert body["runs"] == []


def test_another_tenants_run_is_unreachable_by_identity_too(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """The listing and the detail route have to agree about what does not exist."""
    theirs = _submit(queue, "globex", age=0)

    response = client.get(
        f"/v1/runs/{theirs.run_id}", headers={"Authorization": f"Bearer {ACME}"}
    )

    assert response.status_code == 404


# -- paging -------------------------------------------------------------------


def test_paging_returns_every_run_exactly_once(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """SC-009. Five runs, pages of two, no duplicate and no omission."""
    submitted = [_submit(queue, "acme", age=age) for age in range(5)]

    seen = _walk(client, ACME, limit=2)

    assert len(seen) == len(set(seen)), "a run appeared twice"
    assert set(seen) == {str(run.run_id) for run in submitted}


def test_a_run_submitted_between_two_pages_causes_no_repeat(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """The half an `OFFSET` page fails, and the reason for keyset paging.

    The new run sorts newest-first, ahead of the cursor, so it is legitimately
    absent from this walk. What must not happen is a run already seen appearing
    again because everything shifted by one.
    """
    [_submit(queue, "acme", age=age) for age in range(4)]

    first = client.get(
        "/v1/runs", params={"limit": 2}, headers={"Authorization": f"Bearer {ACME}"}
    ).json()
    seen = [row["run_id"] for row in first["runs"]]

    _submit(queue, "acme", age=-1)  # newer than everything already listed

    rest = client.get(
        "/v1/runs",
        params={"limit": 2, "cursor": first["next_cursor"]},
        headers={"Authorization": f"Bearer {ACME}"},
    ).json()
    seen.extend(row["run_id"] for row in rest["runs"])

    assert len(seen) == len(set(seen)), "an insertion shifted the page and repeated a run"


def test_the_order_is_newest_first_and_stable_across_pages(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    submitted = [_submit(queue, "acme", age=age) for age in range(5)]

    seen = _walk(client, ACME, limit=2)

    assert seen == [str(run.run_id) for run in submitted]  # age 0 first, age 4 last


# -- removal ------------------------------------------------------------------


def test_a_removed_run_leaves_the_listing_and_stays_reachable_by_identity(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """FR-020 and `specs/010` FR-011, which have to hold at the same time."""
    kept = _submit(queue, "acme", age=0)
    removed = _submit(queue, "acme", age=1)
    queue.entomb([removed], now=NOW, policy="retention")

    assert _walk(client, ACME) == [str(kept.run_id)]

    stone = client.get(
        f"/v1/runs/{removed.run_id}", headers={"Authorization": f"Bearer {ACME}"}
    )
    assert stone.status_code == 410
    assert stone.json()["policy"] == "retention"


def test_a_removed_run_is_a_404_to_another_tenant(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """Being told about a tombstone is a disclosure; only the owner gets one."""
    removed = _submit(queue, "acme", age=0)
    queue.entomb([removed], now=NOW, policy="retention")

    response = client.get(
        f"/v1/runs/{removed.run_id}", headers={"Authorization": f"Bearer {GLOBEX}"}
    )

    assert response.status_code == 404
