"""SC-009: what a deployment does when it loses its database.

Three claims, and the third is the one that matters:

* liveness passes in **100%** of probes — because a liveness probe that failed
  here would restart every replica for a fault none of them has, turning a
  database outage into a fleet outage;
* readiness fails in **100%**, naming `run-state-database` — strict, and it
  withdraws working synchronous capacity on purpose (FR-087);
* run submission is refused with a retryable 503 in **100%**, and accepted-then-
  lost in **0%**. That is the honest half. A run accepted into a database that
  cannot record it is work nobody will ever do and nobody will ever be told
  about, which is the silent failure FR-057 exists to prevent.

**The database really is unreachable here.** The queue is a `PostgresRunQueue`
pointed at a port nothing listens on, not a fake told to raise. The distinction
earns its keep: the code path under test is `psycopg`'s failure being mapped to
`RunStateUnavailableError` at the boundary, and a fake that raised that error
directly would skip the mapping and test the assertion rather than the system.

No container needed, and no `postgres` marker: an unreachable database is
available on every machine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.runs.health import DOCUMENT_STORE, RUN_STATE_DATABASE

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"

#: Port 1 is reserved and nothing binds it. `connect_timeout` keeps the refusal
#: prompt even where a firewall drops rather than resets.
DEAD_DSN = "postgresql://docdoc:docdoc@127.0.0.1:1/docdoc?connect_timeout=1"

#: Enough probes that "100%" is a measurement rather than a single observation.
PROBES = 20


@pytest.fixture
def stopped_database(tmp_path: Path) -> TestClient:
    """A deployment configured for runs, whose database is not there."""
    psycopg = pytest.importorskip("psycopg")
    from docdoc.runs.postgres import PostgresRunQueue

    deployment = _Deployment(
        store=FileArtifactStore(tmp_path),
        blobs=BlobStore(tmp_path),
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        runs=PostgresRunQueue(lambda: psycopg.connect(DEAD_DSN)),
    )
    return TestClient(build_app(deployment))


def _blob(client: TestClient) -> str:
    response = client.post("/v1/documents", content=FIXTURE.read_bytes())
    assert response.status_code == 200
    return str(response.json()["blob_id"])


def test_liveness_passes_every_probe_with_the_database_down(
    stopped_database: TestClient,
) -> None:
    """FR-053. Liveness touches nothing, so nothing can make it fail."""
    statuses = [stopped_database.get("/healthz").status_code for _ in range(PROBES)]

    assert statuses == [200] * PROBES, (
        "liveness observed the database outage. A liveness probe that checks a "
        "dependency restarts every replica for a fault none of them has"
    )
    assert stopped_database.get("/healthz").json() == {"status": "alive"}


def test_readiness_fails_every_probe_and_names_the_dependency(
    stopped_database: TestClient,
) -> None:
    """FR-054, FR-055, FR-087 — and the 100% is the assertion, not the naming.

    A readiness check that answered ready on some probes because a cache held a
    stale success would be worse than one that never worked: it would put the
    replica back into rotation intermittently.
    """
    observed = [stopped_database.get("/readyz") for _ in range(PROBES)]

    assert [r.status_code for r in observed] == [503] * PROBES
    for response in observed:
        body = response.json()
        assert body["status"] == "not_ready"
        assert RUN_STATE_DATABASE in body["unmet"], (
            "readiness failed without saying what is unmet, which sends an "
            "operator to read logs for something the probe already knew"
        )
        # The store is fine; only the database is down. A readiness check that
        # reported both would teach an operator to ignore the list.
        assert DOCUMENT_STORE not in body["unmet"]


def test_submission_is_refused_retryably_and_never_accepted(
    stopped_database: TestClient,
) -> None:
    """FR-057 and gate 9: refuse, rather than accept work that cannot be recorded.

    The 0% half is checked by the absence of a `run_id` in every response. A body
    carrying one would be a receipt for a run that does not exist, and the caller
    would poll it forever.
    """
    blob_id = _blob(stopped_database)

    responses = [
        stopped_database.post(f"/v1/documents/{blob_id}/runs", params={"schema": SCHEMA})
        for _ in range(PROBES)
    ]

    assert [r.status_code for r in responses] == [503] * PROBES
    for response in responses:
        assert "run_id" not in response.json(), (
            "a run was accepted although it could not be recorded; the caller "
            "holds an identity that will never resolve"
        )
        assert response.json()["error"]["class"] == "RunStateUnavailableError"
        assert response.headers.get("retry-after"), (
            "503 without Retry-After tells a client to give up rather than to "
            "come back, which is the opposite of what this status means here"
        )


def test_the_synchronous_routes_still_work_which_is_why_strictness_is_a_choice(
    stopped_database: TestClient,
) -> None:
    """FR-087's cost, asserted rather than asserted-about.

    Readiness reports not ready while this route serves a complete, correct
    result. That is the working capacity being withdrawn on purpose. Pinning it
    means the trade-off is visible to whoever next proposes to make readiness
    per-capability — the answer is that no orchestrator's probe can express it.
    """
    pytest.importorskip("pymupdf")
    blob_id = _blob(stopped_database)

    result = stopped_database.post(f"/v1/documents/{blob_id}/extract", params={"schema": SCHEMA})

    assert result.status_code == 200
    assert result.json()["job_id"]
    assert stopped_database.get("/readyz").status_code == 503
