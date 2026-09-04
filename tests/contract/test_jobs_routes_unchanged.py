"""FR-008, FR-009, FR-010 — what Milestone 9 did *not* change.

Adding asynchrony to a system whose job model was deliberately synchronous is
where a compatible change turns into an incompatible one, and the pressure is
specific: `GET /v1/jobs/{job_id}` has three statuses and the obvious thing to do
with a queue is add a fourth. It must not, and the reason is not conservatism.

`job_id` **is** `processing_id`, which is the terminal artifact's identity
(ADR-0003). A queued run has not produced one and cannot: the id is derived from
stage outputs that do not exist yet. So there is no identifier under which
`pending` could be reported, and inventing one would hand a caller something that
resolves to nothing — which is how a client waits forever.

That is why asynchrony is a new *resource*. `run_id` is opaque and exists from
the moment a request is accepted; `processing_id` is content-addressed and exists
once the run completes (ADR-0013 §1).

This file pins the three-status set, the absence of `pending`, and the absence of
an asynchronous variant of `POST /v1/extract` — the storeless route, which cannot
have one, because there is nowhere to put a result nobody is waiting for.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.api.models import JobStatus
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"
ABSENT_ID = "sha256:" + "0" * 64

#: Milestone 8's set, restated here rather than imported, so that a change to the
#: enum is a change this file objects to. Importing it would make the assertion
#: "the enum equals itself".
MILESTONE_8_STATUSES = {"succeeded", "unavailable", "unknown"}


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(
        build_app(
            _Deployment(
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
            )
        )
    )


def _produce(client: TestClient) -> str:
    """Run a document synchronously and return its `processing_id`."""
    pytest.importorskip("pymupdf")
    blob_id = client.post("/v1/documents", content=FIXTURE.read_bytes()).json()["blob_id"]
    result = client.post(f"/v1/documents/{blob_id}/extract", params={"schema": SCHEMA})
    assert result.status_code == 200, result.text
    return str(result.json()["job_id"])


# -- the closed set ------------------------------------------------------------


def test_the_status_set_is_still_exactly_three(client: TestClient) -> None:
    """FR-008. The enum is the contract, so the enum is what is pinned."""
    assert {status.value for status in JobStatus} == MILESTONE_8_STATUSES


def test_pending_never_appears_for_a_queued_run(tmp_path: Path) -> None:
    """The specific pressure, exercised rather than argued.

    A deployment with a run queue, a queued run in it, and a job route that still
    knows nothing about either — because the run has produced no artifact
    identity, so there is nothing to ask the job route about.
    """
    from dataclasses import dataclass
    from datetime import UTC, datetime, timedelta

    from tests.fixtures.run_queue import InMemoryRunQueue

    from docdoc.runs.identity import new_run_id
    from docdoc.runs.model import DEFAULT_TENANT

    @dataclass
    class Spec:
        blob_id: str = ABSENT_ID
        tenant_id: str = DEFAULT_TENANT
        schema_identity: str = SCHEMA
        request_id: str | None = None
        idempotency_key: str | None = None

    queue = InMemoryRunQueue()
    now = datetime(2026, 9, 3, tzinfo=UTC)
    run = queue.submit(
        Spec(),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=now,
        expires_at=now + timedelta(days=30),
    )
    client = TestClient(
        build_app(
            _Deployment(
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                runs=queue,
            )
        )
    )

    # The run is queued and visible on its own resource…
    assert client.get(f"/v1/runs/{run.run_id}").json()["status"] == "queued"

    # …and the job routes report nothing about it, because there is no
    # `processing_id` to report it under. Every job answer stays in the set.
    for probe in (ABSENT_ID, str(run.run_id), "not-an-identity"):
        body = client.get(f"/v1/jobs/{probe}").json()
        assert body["status"] in MILESTONE_8_STATUSES
        assert body["status"] != "pending"


def test_a_well_formed_absent_identity_is_still_unavailable(client: TestClient) -> None:
    """FR-009. The status a Milestone 8 caller expects, unchanged."""
    response = client.get(f"/v1/jobs/{ABSENT_ID}")

    assert response.status_code == 200
    assert response.json()["status"] == "unavailable"


def test_a_malformed_identity_is_still_unknown(client: TestClient) -> None:
    response = client.get("/v1/jobs/not-an-identity")

    assert response.status_code == 200
    assert response.json()["status"] == "unknown"


def test_a_produced_result_is_still_succeeded_and_retrievable(client: TestClient) -> None:
    """The happy path, which a compatibility test that only checked failures misses."""
    processing_id = _produce(client)

    status = client.get(f"/v1/jobs/{processing_id}")
    result = client.get(f"/v1/jobs/{processing_id}/result")

    assert status.status_code == 200
    assert status.json()["status"] == "succeeded"
    assert result.status_code == 200
    assert result.json()["job_id"] == processing_id


# -- no asynchronous variant of the storeless route ----------------------------


def test_the_storeless_route_has_no_asynchronous_variant(client: TestClient) -> None:
    """FR-010, asserted on the route table rather than by trying a URL.

    `POST /v1/extract` writes nothing, so an asynchronous variant would have
    nowhere to leave a result for a caller who is no longer connected. The route
    simply must not exist, and an exhaustive check is the only way to assert an
    absence.
    """
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]

    assert "/v1/extract/runs" not in paths
    assert "/v1/runs" not in paths, "there is no collection route that submits a run"
    run_routes = {path for path in paths if "runs" in path}
    assert run_routes == {"/v1/documents/{blob_id}/runs", "/v1/runs/{run_id}"}, (
        f"an unexpected run route exists: {sorted(run_routes)}. Every asynchronous "
        f"route is anchored to a stored blob, because a run nobody is waiting for "
        f"needs somewhere its input already lives"
    )


def test_the_synchronous_routes_carry_no_new_parameters(client: TestClient) -> None:
    """FR-009's "request shape" half.

    A new optional query parameter is the compatible-looking change that makes a
    route's behaviour depend on something a Milestone 8 caller never sends — and
    on whatever the default turns out to be.
    """
    paths = client.app.openapi()["paths"]  # type: ignore[attr-defined]

    extract = paths["/v1/documents/{blob_id}/extract"]["post"]
    names = {parameter["name"] for parameter in extract.get("parameters", ())}

    assert names == {"blob_id", "schema"}, (
        f"the synchronous extract route gained {sorted(names - {'blob_id', 'schema'})}"
    )
