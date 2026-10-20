"""`started_at` and `error_reason`, against a real database and over HTTP.

Console monitoring plan (``docs/superpowers/sdd/2026-10-20-console-monitoring``),
Task 1: a run gains the instant of its most recent claim and a content-free
reason for why it failed. `tests/unit/test_claim_policy.py` and
`tests/unit/test_claim_order.py` already ask the claim policy's questions
against `InMemoryRunQueue`; this file asks the same two questions of
`PostgresRunQueue` (so the real `UPDATE` statements are the thing under test,
not the fake that stands in for them), plus the one question only the HTTP
layer can answer: that the two fields actually reach a caller's response body.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from tests.infra import require_database

from docdoc.runs import migrations
from docdoc.runs.identity import DEFAULT_LEASE, new_run_id
from docdoc.runs.model import DEFAULT_TENANT, RunOutcome, RunStatus
from docdoc.runs.postgres import PostgresRunQueue
from docdoc.runs.principal import digest_of

LEASE = DEFAULT_LEASE


@dataclass
class Spec:
    blob_id: str
    tenant_id: str = DEFAULT_TENANT
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


def _submit(queue: PostgresRunQueue, *, at: datetime, blob_id: str = "sha256:" + "a" * 64):
    return queue.submit(
        Spec(blob_id=blob_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=at,
        expires_at=at + timedelta(days=30),
    )


@pytest.mark.postgres
def test_a_second_claim_moves_started_at(queue: PostgresRunQueue) -> None:
    """A redelivery is a second claim, and `started_at` names the live attempt.

    Mirrors `tests/unit/test_claim_policy.py::test_a_second_claim_moves_started_at`
    against the real `UPDATE … SET status = 'running', …` statement.
    """
    t0 = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    submitted = _submit(queue, at=t0)

    first = queue.claim(worker_id="w1", now=t0, lease=LEASE, max_attempts=3)
    assert first is not None
    assert first.started_at == t0

    t1 = t0 + LEASE + timedelta(seconds=1)
    redelivered = queue.claim(worker_id="w2", now=t1, lease=LEASE, max_attempts=3)

    assert redelivered is not None
    assert redelivered.run_id == submitted.run_id
    assert redelivered.started_at == t1


@pytest.mark.postgres
def test_finish_writes_error_reason(queue: PostgresRunQueue) -> None:
    """`error_reason` reaches the row the same way `error_class` always has."""
    now = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    submitted = _submit(queue, at=now)
    claimed = queue.claim(worker_id="w1", now=now, lease=LEASE, max_attempts=3)
    assert claimed is not None

    queue.finish(
        submitted.run_id,
        RunOutcome(
            status=RunStatus.FAILED,
            failed_stage="parse",
            error_class="ProviderError",
            error_reason="timeout",
        ),
        now=now,
        worker_id="w1",
    )

    run = queue.get(submitted.run_id, DEFAULT_TENANT)
    assert run is not None
    assert run.status is RunStatus.FAILED
    assert run.error_reason == "timeout"


# -- over HTTP: the two fields actually reach a caller ------------------------

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient  # noqa: E402

from docdoc.api.app import _Deployment, build_app  # noqa: E402

OWNER_KEY = "owner-key"


@pytest.fixture
def client(tmp_path, queue: PostgresRunQueue, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps({"keys": [{"sha256": digest_of(OWNER_KEY), "tenant_id": DEFAULT_TENANT}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))
    return TestClient(build_app(_Deployment(runs=queue)))


@pytest.mark.postgres
def test_the_public_run_body_carries_the_two_fields(
    client: TestClient, queue: PostgresRunQueue
) -> None:
    now = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
    submitted = _submit(queue, at=now)
    claimed = queue.claim(worker_id="w1", now=now, lease=LEASE, max_attempts=3)
    assert claimed is not None
    queue.finish(
        submitted.run_id,
        RunOutcome(
            status=RunStatus.FAILED,
            failed_stage="parse",
            error_class="ProviderError",
            error_reason="timeout",
        ),
        now=now,
        worker_id="w1",
    )

    response = client.get(
        f"/v1/runs/{submitted.run_id}", headers={"Authorization": f"Bearer {OWNER_KEY}"}
    )
    assert response.status_code == 200
    body = response.json()
    assert "started_at" in body
    assert body["started_at"] is not None
    assert body["error_reason"] == "timeout"
