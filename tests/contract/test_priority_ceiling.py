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

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.api.app import _Deployment, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters import EchoAdapter
from docdoc.runs.model import Priority
from docdoc.runs.principal import digest_of

DOCUMENT = Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes()


@pytest.fixture(autouse=True)
def _forget_ceilings():
    """`_CEILINGS` is module-global and `build_app` writes it.

    Without this, a test that grants `acme` an `urgent` ceiling grants it for
    every test that runs afterwards — which is the third time global state set
    by a startup helper has leaked into later tests in this project, and the
    first two took a CI-only failure each to find.
    """
    from docdoc.api.app import _CEILINGS

    before = dict(_CEILINGS)
    yield
    _CEILINGS.clear()
    _CEILINGS.update(before)


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


# -- the per-tenant ceiling, from the limits file ----------------------------
#
# The deployment-wide ceiling above was tested from the day it landed; the
# per-tenant one was not, and it is the half an operator actually uses. A
# deployment grants one customer `urgent` and everybody else `ordinary` — a
# reading that silently applied the deployment default to all of them would look
# identical in every test above.


def _client_with_limits(tmp_path: Path, raw: dict, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A deployment whose limits file names ceilings, read at `build_app`.

    Read once at startup for the reason the key file is: a customer's ceiling is
    a configuration change, and a deployment restarts for every other one.
    """
    import json

    limits = tmp_path / "limits.json"
    limits.write_text(json.dumps(raw), encoding="utf-8")
    monkeypatch.setenv("DOCDOC_LIMITS_FILE", str(limits))

    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps(
            {
                "keys": [
                    {"sha256": digest_of("acme-key"), "tenant_id": "acme"},
                    {"sha256": digest_of("globex-key"), "tenant_id": "globex"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))

    return TestClient(
        build_app(
            _Deployment(
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                runs=InMemoryRunQueue(),
            )
        )
    )


def _submit_as(client: TestClient, key: str, body: dict | None = None):
    headers = {"Authorization": f"Bearer {key}"}
    blob = client.post("/v1/documents", content=DOCUMENT, headers=headers).json()["blob_id"]
    return client.post(
        f"/v1/documents/{blob}/runs", params={"schema": "invoice@1"}, json=body, headers=headers
    )


def test_a_tenant_named_in_the_limits_file_gets_its_own_ceiling(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-087b. One customer is granted `urgent`; the deployment default is not
    moved for anybody else."""
    client = _client_with_limits(
        tmp_path, {"tenants": {"acme": {"priority_ceiling": "urgent"}}}, monkeypatch
    )

    granted = _submit_as(client, "acme-key", {"priority": "urgent"})
    refused = _submit_as(client, "globex-key", {"priority": "urgent"})

    assert granted.json()["priority"] == Priority.URGENT.label
    assert refused.json()["priority"] == Priority.ORDINARY.label, (
        "a ceiling granted to one tenant was applied to another"
    )


def test_a_ceiling_the_file_does_not_name_falls_back_to_the_deployment_default(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An entry with limits but no ceiling is not an entry with a ceiling of zero."""
    monkeypatch.setenv("DOCDOC_PRIORITY_CEILING", "urgent")
    client = _client_with_limits(
        tmp_path, {"tenants": {"acme": {"concurrent_runs": 2}}}, monkeypatch
    )

    assert _submit_as(client, "acme-key", {"priority": "urgent"}).json()["priority"] == (
        Priority.URGENT.label
    )


def test_a_misspelled_ceiling_grants_nothing_rather_than_guessing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`"high"` is not a priority. Guessing which one was meant is how a customer
    ends up in front of another customer's queue on a typo."""
    client = _client_with_limits(
        tmp_path, {"tenants": {"acme": {"priority_ceiling": "high"}}}, monkeypatch
    )

    assert _submit_as(client, "acme-key", {"priority": "urgent"}).json()["priority"] == (
        Priority.ORDINARY.label
    )


def test_an_unreadable_limits_file_is_refused_once_and_by_the_limits_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One configuration mistake, reported once.

    `LimitBook.from_file` reads this file first and refuses loudly, so the
    deployment never starts — which is the right answer, because a limits file
    nobody could read is a deployment that believes it is protected.

    `_load_ceilings` reads the *same* file and swallows the same failure on
    purpose. Failing again there would report the mistake twice; falling back to
    the deployment-wide default grants nothing, which is the safe direction. It
    is asserted directly because the loud refusal above means no request can
    reach it.
    """
    from docdoc.api.app import _CEILINGS, _load_ceilings

    limits = tmp_path / "limits.json"
    limits.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv("DOCDOC_LIMITS_FILE", str(limits))

    with pytest.raises(ValueError, match="not valid JSON"):
        build_app(
            _Deployment(
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                runs=InMemoryRunQueue(),
            )
        )

    _load_ceilings()
    assert _CEILINGS == {}

    # And a file that is gone by the time this reads it, which is the other way
    # the two reads can disagree.
    limits.unlink()
    _load_ceilings()
    assert _CEILINGS == {}


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
