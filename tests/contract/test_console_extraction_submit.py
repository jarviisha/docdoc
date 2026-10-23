"""`POST /v1/console/tenants/{tenant_id}/extractions` stores a file and submits a run for it.

The two Bearer calls (`POST /v1/documents`, then `POST /v1/documents/{blob_id}/runs`)
in one session-only call, on a named tenant's behalf. It shares their storage path
and their submission, so the limiter, refusals and idempotency are not copied.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.limiter import InMemoryLimiter
from tests.fixtures.run_queue import InMemoryRunQueue
from tests.support.console import ADMIN_KEY, PLAIN_KEY, H, Ring, accounts_with_alice, sign_in

from docdoc.api.app import _Deployment, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters import EchoAdapter
from docdoc.runs.limits import LimitPolicy
from docdoc.runs.model import RunKind

DOCUMENT = Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes()
URL = "/v1/console/tenants/acme/extractions"
INVOICE = {"schema": "invoice@1"}


@pytest.fixture
def queue() -> InMemoryRunQueue:
    return InMemoryRunQueue()


@pytest.fixture
def limiter() -> InMemoryLimiter:
    return InMemoryLimiter(policy=LimitPolicy(submissions_per_minute=2))


@pytest.fixture
def signed_in(
    tmp_path: Path, queue: InMemoryRunQueue, limiter: InMemoryLimiter
) -> tuple[TestClient, dict[str, str]]:
    client = TestClient(
        build_app(
            _Deployment(
                store=FileArtifactStore(tmp_path),
                blobs=BlobStore(tmp_path),
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                runs=queue,
                limiter=limiter,
                console=accounts_with_alice(),
                keys=Ring(),
            )
        )
    )
    return client, sign_in(client)


def test_an_operator_submits_an_extraction(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in

    r = client.post(URL, params=INVOICE, content=DOCUMENT, headers={**cookie, **H})

    assert r.status_code == 202, r.text
    assert r.json()["tenant_id"] == "acme"
    (run,) = queue.page_all_runs(tenant_id="acme", status=None, before=None, limit=10)
    assert str(run.run_id) == r.json()["run_id"]
    assert run.kind == RunKind.EXTRACTION
    assert run.schema_identity == "invoice@1"
    assert r.json()["priority"] == "urgent"
    assert r.json()["status"] == "queued"


def test_an_unknown_schema_is_refused_as_the_bearer_route_refuses_it(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in

    r = client.post(URL, params={"schema": "nope@1"}, content=DOCUMENT, headers={**cookie, **H})

    assert r.status_code == 422
    assert r.json()["error"]["class"] == "SchemaError"
    assert queue.page_all_runs(tenant_id="acme", status=None, before=None, limit=10) == ()


def test_an_empty_body_is_refused(signed_in: tuple[TestClient, dict[str, str]]) -> None:
    client, cookie = signed_in
    same_as = client.post(
        "/v1/documents", content=b"", headers={"Authorization": f"Bearer {PLAIN_KEY}"}
    )

    r = client.post(URL, params=INVOICE, content=b"", headers={**cookie, **H})

    assert same_as.status_code >= 400
    assert r.status_code == same_as.status_code


def test_a_rate_limited_tenant_gets_429(
    signed_in: tuple[TestClient, dict[str, str]], limiter: InMemoryLimiter
) -> None:
    client, cookie = signed_in
    limiter.policy = LimitPolicy(submissions_per_minute=1)
    assert (
        client.post(URL, params=INVOICE, content=DOCUMENT, headers={**cookie, **H}).status_code
        == 202
    )

    r = client.post(URL, params=INVOICE, content=DOCUMENT, headers={**cookie, **H})

    assert r.status_code == 429
    assert "Retry-After" in r.headers


def test_the_same_idempotency_key_replays(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in
    headers = {**cookie, **H, "Idempotency-Key": "once"}

    first = client.post(URL, params=INVOICE, content=DOCUMENT, headers=headers)
    again = client.post(URL, params=INVOICE, content=DOCUMENT, headers=headers)

    assert (first.status_code, again.status_code) == (202, 200)
    assert again.json() == first.json()
    assert len(queue.page_all_runs(tenant_id="acme", status=None, before=None, limit=10)) == 1


def test_an_invalid_tenant_is_422(signed_in: tuple[TestClient, dict[str, str]]) -> None:
    client, cookie = signed_in

    r = client.post(
        "/v1/console/tenants/Not_Valid/extractions",
        params=INVOICE,
        content=DOCUMENT,
        headers={**cookie, **H},
    )

    assert (r.status_code, r.json()) == (422, {"error": "invalid_tenant_id"})


def test_the_submission_is_logged(
    signed_in: tuple[TestClient, dict[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    client, cookie = signed_in

    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        r = client.post(URL, params=INVOICE, content=DOCUMENT, headers={**cookie, **H})

    events = []
    for record in caplog.records:
        try:
            payload = json.loads(record.getMessage())
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("event") == "console.extract_submit":
            events.append(payload)
    assert events == [
        {
            "event": "console.extract_submit",
            "actor": "console:alice",
            "tenant_id": "acme",
            "run_id": r.json()["run_id"],
            "schema": "invoice@1",
            "status": 202,
        }
    ]


def test_bearer_cannot_reach_it(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, _ = signed_in
    client.cookies.clear()

    r = client.post(
        URL,
        params=INVOICE,
        content=DOCUMENT,
        headers={**H, "Authorization": f"Bearer {ADMIN_KEY}"},
    )

    assert r.status_code == 401
    assert queue.page_all_runs(tenant_id="acme", status=None, before=None, limit=10) == ()
