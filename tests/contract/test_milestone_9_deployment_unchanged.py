"""T172, SC-002 — a Milestone 9 deployment observes no behavioural change.

Quickstart scenario 0, as a suite. Every capability this milestone added is off
until configured, so a deployment that changes nothing must sweep nothing, count
nothing, refuse nothing, export nothing, route nothing, and deliver nothing — and
hold zero rows in every table the milestone added.

**This is the claim the whole milestone is measured on**, and it is a claim about
absences. Absences are exactly what a diff does not show: seven capabilities were
added, each of them defaults to off, and one of them defaulting to *on* would be
a one-line change nobody would notice in review and every existing deployment
would notice on upgrade.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.api.app import _Deployment, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters import EchoAdapter
from docdoc.pipeline import observe as pipeline_observe
from docdoc.runs import observe as runs_observe

DOCUMENT = Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes()

#: Every variable Milestone 10 added. A deployment configured as Milestone 9
#: configured it has none of them set, and this fixture makes that true even on a
#: developer's machine that has been experimenting.
MILESTONE_10_SETTINGS = (
    "DOCDOC_RUN_RETENTION_DAYS",
    "DOCDOC_RUN_SWEEP_BATCH",
    "DOCDOC_LIMIT_SUBMISSIONS_PER_MINUTE",
    "DOCDOC_LIMIT_CONCURRENT_RUNS",
    "DOCDOC_LIMIT_RUNS_PER_PERIOD",
    "DOCDOC_LIMIT_TOKENS_PER_PERIOD",
    "DOCDOC_LIMITS_FILE",
    "DOCDOC_PRIORITY_CEILING",
    "DOCDOC_DELIVERY_SECRETS_FILE",
    "DOCDOC_DELIVERY_ALLOW_PRIVATE",
    "DOCDOC_OTLP_ENDPOINT",
    "DOCDOC_OTLP_HEADERS",
    "DOCDOC_ROUTING_POLICY",
    "DOCDOC_CORRECTION_RETENTION_DAYS",
)


@pytest.fixture(autouse=True)
def a_milestone_9_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing this milestone added is configured."""
    for name in MILESTONE_10_SETTINGS:
        monkeypatch.delenv(name, raising=False)
    runs_observe.set_observer(None)
    pipeline_observe.set_observer(None)


@pytest.fixture
def queue() -> InMemoryRunQueue:
    return InMemoryRunQueue()


@pytest.fixture
def client(tmp_path: Path, queue: InMemoryRunQueue) -> TestClient:
    """Exactly what Milestone 9's quickstart builds: a store, a registry, a queue."""
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


# -- nothing is refused -------------------------------------------------------


def test_no_submission_is_refused(client: TestClient) -> None:
    """A deployment configuring no limits counts nothing and refuses nothing."""
    blob_id = _blob(client)

    for _ in range(25):
        response = client.post(f"/v1/documents/{blob_id}/runs", params={"schema": "invoice@1"})
        assert response.status_code == 202, response.text


def test_the_limiter_is_the_null_one(client: TestClient) -> None:
    """Not a test double — `NullLimiter` is the configuration docdoc runs in when
    nobody asked for limits."""
    from docdoc.runs.limits import NullLimiter

    deployment = client.app.state.deployment  # type: ignore[attr-defined]
    assert isinstance(deployment.limiter, NullLimiter)


# -- nothing is routed --------------------------------------------------------


def test_a_run_carries_no_routing_block(client: TestClient) -> None:
    """**Absent, not null** (FR-075). A block saying nothing would be a second
    way of saying "not configured" that nobody documented."""
    blob_id = _blob(client)
    run_id = client.post(f"/v1/documents/{blob_id}/runs", params={"schema": "invoice@1"}).json()[
        "run_id"
    ]

    body = client.get(f"/v1/runs/{run_id}").json()

    assert "routing" not in body
    assert client.app.state.deployment.routing_policy() is None  # type: ignore[attr-defined]


# -- nothing is delivered -----------------------------------------------------


def test_no_deliverer_exists(client: TestClient) -> None:
    """FR-064: no callback registered, no outbound request, and nothing to make one."""
    assert client.app.state.deployment.deliverer() is None  # type: ignore[attr-defined]


def test_registering_a_callback_reports_that_delivery_is_unconfigured(
    client: TestClient,
) -> None:
    """`503` naming what to configure — not a `404`, which would send an operator
    looking for a typo in a URL that is correct."""
    response = client.post(
        "/v1/callbacks", json={"url": "https://example.com/h", "secret": "whsec_x"}
    )

    assert response.status_code == 503
    assert "DOCDOC_DELIVERY_SECRETS_FILE" in response.text


# -- nothing is exported ------------------------------------------------------


def test_no_observer_is_installed(client: TestClient) -> None:
    """SC-014. Unset means nothing is exported, and the extra is not required."""
    assert runs_observe.observer() is None
    assert pipeline_observe.observer() is None


def test_the_telemetry_extra_is_not_required() -> None:
    """A base install imports the module and never the SDK."""
    from docdoc import telemetry

    assert callable(telemetry.bridge)


# -- nothing is swept ---------------------------------------------------------


def test_retention_is_unconfigured() -> None:
    """FR-014. `None` is a real answer and the default one."""
    from docdoc.runs.identity import configured_retention

    assert configured_retention() is None


def test_a_tick_with_no_retention_period_touches_nothing() -> None:
    from datetime import UTC, datetime

    from docdoc.runs import maintenance, retention

    class _Queue:
        calls = 0

        def expiring(self, **_: object) -> tuple:
            type(self).calls += 1
            return ()

    class _Nothing:
        def delete(self, identity: str) -> bool:
            return False

        def delete_prefix(self, *, allow_store_root: bool = False) -> int:
            return 0

    report = maintenance.tick(
        maintenance.MaintenanceDeps(
            queue=_Queue(),
            stores_for=lambda _: retention.Stores(artifacts=_Nothing(), blobs=_Nothing()),
        ),
        now=datetime(2026, 9, 9, tzinfo=UTC),
        retention_period=None,
        batch=500,
    )

    assert _Queue.calls == 0
    assert report.swept.runs == 0


# -- the Milestone 9 surface is unchanged -------------------------------------


def test_the_synchronous_routes_answer_as_they_did(client: TestClient) -> None:
    blob_id = _blob(client)

    metadata = client.get(f"/v1/documents/{blob_id}")
    assert metadata.status_code == 200

    schemas = client.get("/v1/schemas")
    assert schemas.status_code == 200


def test_an_empty_submission_body_is_the_milestone_9_request(client: TestClient) -> None:
    """The request shape did not change: two optional inputs, and no body is fine."""
    blob_id = _blob(client)

    response = client.post(f"/v1/documents/{blob_id}/runs", params={"schema": "invoice@1"})

    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "queued"
    # `priority` is the one addition, and it is echoed always so a client can see
    # what it was granted (FR-087a).
    assert body["priority"] == "ordinary"


def test_the_run_status_set_is_still_five(client: TestClient) -> None:
    """SC-023. A client switching exhaustively on it keeps being right."""
    from docdoc.runs.model import RunStatus

    assert {member.value for member in RunStatus} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }


def test_authentication_is_still_off_by_default(client: TestClient) -> None:
    """FR-088 unchanged, and upgrading grants nobody an administrative scope."""
    deployment = client.app.state.deployment  # type: ignore[attr-defined]

    assert not deployment.ring.enabled
    assert client.get("/v1/schemas").status_code == 200


def test_no_admin_route_is_reachable_without_a_key(client: TestClient) -> None:
    """With authentication off there is one implicit tenant with empty scopes, so
    the administrative surface answers `404` — the same as to any non-admin."""
    for path in (
        "/v1/admin/credentials?tenant_id=acme",
        "/v1/admin/tenants/acme",
    ):
        response = client.get(path) if "credentials" in path else client.delete(path)
        assert response.status_code == 404, path


def test_the_environment_really_is_clean() -> None:
    """Guards the guard: a fixture that missed would make every absence vacuous."""
    assert not [name for name in MILESTONE_10_SETTINGS if os.environ.get(name)]
