"""T140, SC-026 — over the ceiling is accepted *at* the ceiling, never refused.

The refusing design is the obvious one and it is wrong for a reason that only
shows up later: an operator lowering a ceiling would break every client that had
been asking for `urgent` and changed nothing. The client cannot read the ceiling
— deliberately, because a tenant that could read it is one step from a tenant
that can raise it (FR-087b) — so it has no way to know what to ask for instead.

Accepting at the ceiling and stating the granted value makes the ceiling
operable: the operator moves it, nothing breaks, and every client can see what it
got.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.api.app import _Deployment, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters import EchoAdapter
from docdoc.runs.model import Priority

DOCUMENT = Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes()


@pytest.fixture
def queue() -> InMemoryRunQueue:
    return InMemoryRunQueue()


@pytest.fixture
def client(tmp_path: Path, queue: InMemoryRunQueue) -> TestClient:
    return TestClient(
        build_app(
            _Deployment(
                store=FileArtifactStore(tmp_path),
                blobs=BlobStore(tmp_path),
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                runs=queue,
            )
        )
    )


def _blob(client: TestClient) -> str:
    response = client.post("/v1/documents", content=DOCUMENT)
    assert response.status_code in (200, 201), response.text
    return str(response.json()["blob_id"])


def _submit(client: TestClient, blob_id: str, body: dict | None = None):
    return client.post(f"/v1/documents/{blob_id}/runs", params={"schema": "invoice@1"}, json=body)


def test_the_granted_priority_is_echoed_when_none_was_requested(client: TestClient) -> None:
    """FR-087a. Always, so a client never has to compare against a ceiling."""
    response = _submit(client, _blob(client))

    assert response.status_code == 202, response.text
    assert response.json()["priority"] == Priority.ORDINARY.label


def test_an_empty_body_is_the_milestone_9_request(client: TestClient) -> None:
    """SC-002. A caller that sends nothing gets exactly what it always got."""
    blob_id = _blob(client)

    response = client.post(f"/v1/documents/{blob_id}/runs", params={"schema": "invoice@1"})

    assert response.status_code == 202, response.text
    assert response.json()["status"] == "queued"


def test_over_the_ceiling_is_accepted_at_the_ceiling(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The requirement.** No ceiling is configured, so none is granted."""
    response = _submit(client, _blob(client), {"priority": "urgent"})

    assert response.status_code == 202, "an over-ceiling request is not refused"
    assert response.json()["priority"] == Priority.ORDINARY.label, (
        "and it was granted at the ceiling rather than at what was asked for"
    )


def test_a_raised_ceiling_grants_what_was_asked_for(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DOCDOC_PRIORITY_CEILING", "urgent")
    client = TestClient(
        build_app(
            _Deployment(
                store=FileArtifactStore(tmp_path),
                blobs=BlobStore(tmp_path),
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                runs=InMemoryRunQueue(),
            )
        )
    )

    response = _submit(client, _blob(client), {"priority": "urgent"})

    assert response.status_code == 202
    assert response.json()["priority"] == Priority.URGENT.label


def test_a_malformed_priority_is_refused(client: TestClient) -> None:
    """Different from over-the-ceiling, and the difference is the point.

    `priority: "high"` is a client that believes it is asking for something.
    Silently granting `ordinary` would leave that belief in place for ever, where
    a 422 corrects it once.
    """
    response = _submit(client, _blob(client), {"priority": "high"})

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_priority"


def test_the_ceiling_is_readable_through_no_route(client: TestClient) -> None:
    """FR-087b, as an absence.

    A tenant that could read its ceiling is one step from a tenant that can move
    it, and a client that can move its own ceiling has self-service escalation
    past another customer's queue.
    """
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]

    assert not [path for path in paths if "ceiling" in path or "priority" in path]

    body = _submit(client, _blob(client), {"priority": "urgent"}).json()
    assert "ceiling" not in body
    assert set(body) == {"run_id", "status", "created_at", "priority"}


def test_the_run_reports_the_priority_it_carries(client: TestClient) -> None:
    """The same value on the way back out, so a client polling sees what it got."""
    submitted = _submit(client, _blob(client), {"priority": "urgent"}).json()

    state = client.get(f"/v1/runs/{submitted['run_id']}")

    assert state.status_code == 200
    assert state.json()["priority"] == submitted["priority"]
