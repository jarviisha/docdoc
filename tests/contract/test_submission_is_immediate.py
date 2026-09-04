"""SC-002 — submission returns under 200 ms at p95, whatever the document costs.

This is the criterion the whole topology change buys. Before Milestone 9 a
400-page scan held an HTTP connection for the several minutes it took; after it,
the response is a receipt and the work happens elsewhere.

**The worker pool is stopped for the measurement, and that is the design of the
test rather than a convenience.** With a worker running, a fast response would be
ambiguous — it could mean submission is cheap, or it could mean this particular
document happened to be quick. With no worker, no run can possibly complete
during the measurement, so a fast response can only mean the response is not the
run.

**p95 rather than a mean, and rather than a maximum.** A mean hides a slow tail
that a caller would feel; a maximum on a shared CI machine measures whether
another process wanted the CPU. p95 over enough samples is the shape SC-002
states and the one that fails for a reason.

The database is a real one when `DOCDOC_TEST_DATABASE_URL` is set — a submission
writes a row, and a test that stubbed that out would be measuring an in-memory
dictionary. It falls back to the in-memory queue otherwise so the offline suite
still checks the *shape* of the claim: that the size of the document does not
enter the response time.
"""

from __future__ import annotations

import os
import statistics
import time
from pathlib import Path

import pytest
from tests.infra import DATABASE_URL_ENV

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"

#: SC-002's number.
BUDGET_MS = 200.0

#: Enough samples that a 95th percentile is a percentile. Twenty would make p95
#: "the slowest one", which is a maximum wearing a percentile's name.
SAMPLES = 60


def _queue():  # type: ignore[no-untyped-def]
    """A real queue where one is configured, and the fake otherwise.

    Not `require_database()`: this test is worth running offline. What changes
    without a database is the *fidelity* of the number, not the property being
    asserted, and saying so here is better than skipping and measuring nothing on
    every contributor's machine.
    """
    dsn = os.environ.get(DATABASE_URL_ENV)
    if not dsn:
        from tests.fixtures.run_queue import InMemoryRunQueue

        return InMemoryRunQueue()

    psycopg = pytest.importorskip("psycopg")
    from datetime import UTC, datetime

    from docdoc.runs import migrations
    from docdoc.runs.postgres import PostgresRunQueue

    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """A deployment that accepts runs, with **no worker anywhere**.

    Nothing in this process claims, so nothing can complete. That is what makes
    the measurement mean what it says.
    """
    return TestClient(
        build_app(
            _Deployment(
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                runs=_queue(),
            )
        )
    )


def _blob(client: TestClient, data: bytes) -> str:
    response = client.post("/v1/documents", content=data)
    assert response.status_code == 200, response.text
    return str(response.json()["blob_id"])


def _p95(samples: list[float]) -> float:
    ordered = sorted(samples)
    return ordered[min(len(ordered) - 1, round(0.95 * (len(ordered) - 1)))]


def test_submission_returns_under_the_budget_at_p95(client: TestClient) -> None:
    """SC-002. The headline claim of the topology change."""
    blob_id = _blob(client, FIXTURE.read_bytes())

    timings: list[float] = []
    for _ in range(SAMPLES):
        started = time.perf_counter()
        response = client.post(f"/v1/documents/{blob_id}/runs", params={"schema": SCHEMA})
        timings.append((time.perf_counter() - started) * 1000)
        assert response.status_code == 202, response.text

    p95 = _p95(timings)
    assert p95 < BUDGET_MS, (
        f"p95 submission latency was {p95:.1f} ms against a {BUDGET_MS:.0f} ms "
        f"budget (median {statistics.median(timings):.1f} ms). With the worker "
        f"pool stopped, nothing here can be the run — so this is the accepting "
        f"itself having become expensive"
    )


def test_no_run_completed_during_the_measurement(client: TestClient) -> None:
    """Guards the measurement: a fast response from a run that already finished.

    Without this, a change that executed submissions inline on a document the
    echo adapter answers instantly would pass the timing assertion and defeat the
    entire milestone.
    """
    blob_id = _blob(client, FIXTURE.read_bytes())

    accepted = client.post(f"/v1/documents/{blob_id}/runs", params={"schema": SCHEMA})
    run_id = accepted.json()["run_id"]

    state = client.get(f"/v1/runs/{run_id}").json()

    assert state["status"] == "queued", (
        f"the run reached {state['status']!r} with no worker running, so something "
        f"executed it inside the request"
    )
    assert accepted.json().get("processing_id") is None
    assert "processing_id" not in accepted.json(), (
        "the acceptance named a result. `processing_id` is the terminal artifact "
        "id and cannot exist before the stages that derive it (ADR-0013 §1); "
        "absent, not null, so a caller cannot send it to GET /v1/jobs"
    )


def test_a_larger_document_does_not_cost_more_to_submit(client: TestClient, tmp_path: Path) -> None:
    """ "Regardless of document size" is the half a single fixture cannot show.

    A submission that read or parsed the document would scale with it, and that
    is exactly the regression this milestone must not acquire: the whole point is
    that accepting work is decoupled from doing it.
    """
    small = _blob(client, FIXTURE.read_bytes())
    # The same document twenty times over. Not a valid PDF beyond its first
    # bytes, which is irrelevant: submission stores bytes and no parse happens
    # here — and if one ever did, this test would say so loudly.
    large = _blob(client, FIXTURE.read_bytes() * 20)

    def median_ms(blob_id: str) -> float:
        timings = []
        for _ in range(SAMPLES // 2):
            started = time.perf_counter()
            assert (
                client.post(f"/v1/documents/{blob_id}/runs", params={"schema": SCHEMA}).status_code
                == 202
            )
            timings.append((time.perf_counter() - started) * 1000)
        return statistics.median(timings)

    small_ms = median_ms(small)
    large_ms = median_ms(large)

    assert large_ms < BUDGET_MS
    # A generous factor: the assertion is that cost does not *scale* with size,
    # not that two medians on a shared machine are equal. Twenty times the bytes
    # taking under three times the wall clock is decisively sublinear.
    assert large_ms < max(small_ms * 3, 20.0), (
        f"submitting a 20x larger document took {large_ms:.1f} ms against "
        f"{small_ms:.1f} ms. Submission is reading the document, so the response "
        f"time is a function of the work rather than of accepting it"
    )
