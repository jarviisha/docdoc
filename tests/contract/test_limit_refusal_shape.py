"""T100, T101, T102 — what a `429` costs, and what it leaves alone.

Three properties, and the second is the one worth having a test for.

**A refusal names the limit** (FR-043). Being over a quota is not a secret, which
is the opposite of an authentication refusal — that one says nothing at all,
deliberately, and a client told only "no" retries immediately and forever.

**A refused submission creates nothing** (FR-044): no run row, no queue position,
no store access. It costs the deployment one counter read. A limiter that refused
*after* creating the row would leave a queue full of runs nobody accepted.

**Lowering a limit refuses new work and aborts none** (FR-045, FR-046). An
operator tightening a cap must not discard runs somebody already paid for — the
paid-and-discarded failure Milestone 9's whole design existed to remove.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.limiter import InMemoryLimiter
from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.api.app import _Deployment, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters import EchoAdapter
from docdoc.runs.limits import LimitPolicy

#: A real PDF, because the blob route decides the type from the bytes rather than
#: from what a caller declares -- a fabricated header is rejected at the door.
DOCUMENT = Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes()


@pytest.fixture
def queue() -> InMemoryRunQueue:
    return InMemoryRunQueue()


@pytest.fixture
def limiter() -> InMemoryLimiter:
    return InMemoryLimiter(policy=LimitPolicy(submissions_per_minute=1))


@pytest.fixture
def client(tmp_path: Path, queue: InMemoryRunQueue, limiter: InMemoryLimiter) -> TestClient:
    return TestClient(
        build_app(
            _Deployment(
                store=FileArtifactStore(tmp_path),
                blobs=BlobStore(tmp_path),
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                runs=queue,
                limiter=limiter,
            )
        )
    )


def _blob(client: TestClient) -> str:
    # Raw body, not multipart: the route reads the bytes and decides the type
    # from them, which is what every other suite that posts here does.
    response = client.post("/v1/documents", content=DOCUMENT)
    assert response.status_code in (200, 201), response.text
    return str(response.json()["blob_id"])


def _submit(client: TestClient, blob_id: str) -> object:
    return client.post(f"/v1/documents/{blob_id}/runs?schema=invoice@1")


class TestTheRefusalNamesTheLimit:
    def test_429_carries_four_facts_and_a_retry_after(
        self, client: TestClient, queue: InMemoryRunQueue
    ) -> None:
        blob_id = _blob(client)

        assert _submit(client, blob_id).status_code == 202  # type: ignore[attr-defined]
        refused = _submit(client, blob_id)

        assert refused.status_code == 429  # type: ignore[attr-defined]
        body = refused.json()  # type: ignore[attr-defined]
        assert body["limit"] == "submissions"
        assert (body["observed"], body["allowed"]) == (1, 1)
        assert body["retry_after_seconds"] > 0
        assert refused.headers["Retry-After"] == str(body["retry_after_seconds"])  # type: ignore[attr-defined]

    def test_it_is_not_confusable_with_an_authentication_refusal(self, client: TestClient) -> None:
        """The two must not look alike: one says come back, the other says nothing."""
        blob_id = _blob(client)
        _submit(client, blob_id)

        refused = _submit(client, blob_id)

        assert refused.status_code not in (401, 403)  # type: ignore[attr-defined]
        assert "Retry-After" in refused.headers  # type: ignore[attr-defined]


class TestARefusalCreatesNothing:
    """FR-044 — one counter read, and no side effect anywhere."""

    def test_no_run_row_exists(self, client: TestClient, queue: InMemoryRunQueue) -> None:
        blob_id = _blob(client)
        _submit(client, blob_id)

        _submit(client, blob_id)

        assert len(queue._runs) == 1, (
            "a refused submission created a run. FR-044: no run, no queue "
            "position, no store access -- a limiter that refuses after creating "
            "the row leaves a queue full of runs nobody accepted."
        )

    def test_the_counter_is_not_spent_by_a_refusal(
        self, client: TestClient, limiter: InMemoryLimiter
    ) -> None:
        """Otherwise a refused client would push its own retry further away."""
        blob_id = _blob(client)
        _submit(client, blob_id)
        spent = dict(limiter.counters)

        _submit(client, blob_id)

        assert limiter.counters == spent


class TestLoweringALimitAbortsNothing:
    """FR-045, FR-046 — tightening a cap is not a way to cancel work."""

    def test_runs_in_flight_are_untouched(
        self, client: TestClient, queue: InMemoryRunQueue, limiter: InMemoryLimiter
    ) -> None:
        blob_id = _blob(client)
        assert _submit(client, blob_id).status_code == 202  # type: ignore[attr-defined]
        accepted = next(iter(queue._runs.values()))

        # The operator tightens the cap below current usage.
        limiter.policy = LimitPolicy(submissions_per_minute=0, concurrent_runs=0)

        assert _submit(client, blob_id).status_code == 429  # type: ignore[attr-defined]
        assert queue.get(accepted.run_id, accepted.tenant_id) is not None
        assert queue.get(accepted.run_id, accepted.tenant_id).status is accepted.status  # type: ignore[union-attr]

    def test_there_is_no_verb_that_could(self, limiter: InMemoryLimiter) -> None:
        """Asserted structurally: the protocol offers no way to express it."""
        for forbidden in ("abort", "cancel", "revoke", "terminate"):
            assert not hasattr(limiter, forbidden)
