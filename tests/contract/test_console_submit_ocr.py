"""The console submits OCR for a named tenant, and lists tenants and parsers (018).

`POST /v1/console/tenants/{tenant_id}/ocr` is `POST /v1/ocr` with the tenant
named in the path instead of carried by a Bearer key: the same function serves
both, so caps, validation, blob storage and idempotency are shared, not copied.
Its success body is the console's run detail. `GET /v1/console/tenants` is the
union of every place a tenant can be known from; `GET /v1/console/parsers` is
`GET /v1/parsers`, behind a session.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue
from tests.infra import require_database
from tests.support.console import ADMIN_KEY, PLAIN_KEY, H, Ring, accounts_with_alice, sign_in
from tests.support.ocr import CountingPost, paddle_registry

from docdoc.api.app import _Deployment, build_app
from docdoc.api.deps import _load_ceilings
from docdoc.artifacts.paths import root_tenant
from docdoc.runs.model import RunKind

IMAGE = (Path(__file__).parent.parent / "fixtures" / "image" / "sample_page.png").read_bytes()
OCR = "/v1/console/tenants/acme/ocr"


@pytest.fixture(autouse=True)
def _no_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DOCDOC_LIMITS_FILE", raising=False)
    _load_ceilings()


@pytest.fixture
def queue() -> InMemoryRunQueue:
    return InMemoryRunQueue()


@pytest.fixture
def deployment(tmp_path: Path, queue: InMemoryRunQueue) -> _Deployment:
    return _Deployment(
        store_root=tmp_path,
        runs=queue,
        console=accounts_with_alice(),
        keys=Ring(),
        parsers=paddle_registry(CountingPost()),
        ocr_queue_per_tenant=1,
        ocr_queue_total=10,
    )


@pytest.fixture
def signed_in(deployment: _Deployment) -> tuple[TestClient, dict[str, str]]:
    client = TestClient(build_app(deployment))
    return client, sign_in(client)


def _blob_count(root: Path) -> int:
    return sum(1 for path in root.rglob("*") if path.is_file() and "blobs" in path.parts)


def test_console_submits_ocr_for_the_named_tenant(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in

    r = client.post(OCR, content=IMAGE, headers={**cookie, **H})

    assert r.status_code == 202
    body = r.json()
    assert body["kind"] == "ocr"
    assert body["tenant_id"] == "acme"
    assert body["status"] == "queued"
    assert body["finished_at"] is None
    assert queue.count_active(kind=RunKind.OCR, tenant_id="acme") == 1


def test_console_ocr_replays_idempotently(
    signed_in: tuple[TestClient, dict[str, str]], deployment: _Deployment
) -> None:
    # Room for the replay: the caps are counted before idempotency is, as on /v1/ocr.
    deployment.ocr_queue_per_tenant = 2
    client, cookie = signed_in
    headers = {**cookie, **H, "Idempotency-Key": "once"}

    first = client.post(OCR, content=IMAGE, headers=headers)
    again = client.post(OCR, content=IMAGE, headers=headers)

    assert (first.status_code, again.status_code) == (202, 200)
    assert again.json() == first.json()
    assert again.json()["tenant_id"] == "acme"


def test_console_ocr_passes_parser_and_language(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in

    r = client.post(
        OCR,
        params={"parser": "paddle", "language": "vie"},
        content=IMAGE,
        headers={**cookie, **H},
    )

    assert r.status_code == 202
    (run,) = queue.page_all_runs(tenant_id="acme", status=None, before=None, limit=10)
    assert (run.ocr_parser, run.ocr_language) == ("paddle", "vie")


def test_console_ocr_refuses_an_unknown_parser(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue, tmp_path: Path
) -> None:
    client, cookie = signed_in

    r = client.post(OCR, params={"parser": "nope"}, content=IMAGE, headers={**cookie, **H})

    assert r.status_code == 422
    assert queue.count_active(kind=RunKind.OCR) == 0
    assert _blob_count(tmp_path) == 0


def test_console_ocr_needs_the_console_header(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in

    r = client.post(OCR, content=IMAGE, headers=cookie)

    assert r.status_code == 403
    assert queue.count_active(kind=RunKind.OCR) == 0


def test_console_ocr_refuses_a_bad_tenant(
    signed_in: tuple[TestClient, dict[str, str]], queue: InMemoryRunQueue
) -> None:
    client, cookie = signed_in

    r = client.post("/v1/console/tenants/Not_Valid/ocr", content=IMAGE, headers={**cookie, **H})

    assert r.status_code == 422
    assert r.json() == {"error": "invalid_tenant_id"}
    assert queue.count_active(kind=RunKind.OCR) == 0


def test_console_and_tenant_ocr_share_the_caps(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, cookie = signed_in
    tenant = client.post("/v1/ocr", content=IMAGE, headers={"Authorization": f"Bearer {PLAIN_KEY}"})
    assert tenant.status_code == 202

    r = client.post(OCR, content=IMAGE, headers={**cookie, **H})

    assert r.status_code == 429
    assert r.headers["Retry-After"] == "30"
    assert r.json() == {
        "error": "limit_exceeded",
        "limit": "ocr_queue",
        "observed": 1,
        "allowed": 1,
        "retry_after_seconds": 30,
    }


def test_console_tenants_without_a_database_is_root_and_ring(tmp_path: Path) -> None:
    """With no credentials table and no runs, those two sources add nothing."""
    deployment = _Deployment(store_root=tmp_path, console=accounts_with_alice(), keys=Ring())
    client = TestClient(build_app(deployment))

    r = client.get("/v1/console/tenants", headers=sign_in(client))

    assert r.status_code == 200
    assert r.json() == {"tenants": sorted({root_tenant(), "acme", "ops"})}


@pytest.mark.postgres
def test_console_tenants_unions_every_source(tmp_path: Path) -> None:
    """Root, a key-file tenant, a credentials-table tenant and a runs-only tenant."""
    psycopg = pytest.importorskip("psycopg")
    from docdoc.runs import migrations
    from docdoc.runs.postgres import PostgresRunQueue

    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs, credentials")
    queue = PostgresRunQueue(lambda: psycopg.connect(dsn))
    deployment = _Deployment(
        store_root=tmp_path,
        runs=queue,
        console=accounts_with_alice(),
        keys=Ring(),
        parsers=paddle_registry(CountingPost()),
    )
    deployment.keys.issue(tenant_id="tabled", scopes=frozenset(), label=None, now=datetime.now(UTC))
    client = TestClient(build_app(deployment))
    cookie = sign_in(client)
    assert (
        client.post(
            "/v1/console/tenants/runsonly/ocr", content=IMAGE, headers={**cookie, **H}
        ).status_code
        == 202
    )
    # Twice, so the union is seen to be unique.
    deployment.keys.issue(tenant_id="acme", scopes=frozenset(), label=None, now=datetime.now(UTC))

    r = client.get("/v1/console/tenants", headers=cookie)

    assert r.status_code == 200
    assert r.json() == {"tenants": sorted({root_tenant(), "acme", "ops", "tabled", "runsonly"})}


def test_console_parsers_matches_v1_parsers(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, cookie = signed_in

    console = client.get("/v1/console/parsers", headers=cookie)
    tenant = client.get("/v1/parsers", headers={"Authorization": f"Bearer {PLAIN_KEY}"})

    assert console.status_code == tenant.status_code == 200
    assert console.json() == tenant.json()
    assert console.json()["parsers"][0]["id"] == "paddle"


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/v1/console/tenants"),
        ("GET", "/v1/console/parsers"),
        ("POST", OCR),
    ],
)
def test_the_new_console_routes_refuse_a_bearer_key(
    signed_in: tuple[TestClient, dict[str, str]],
    queue: InMemoryRunQueue,
    method: str,
    path: str,
) -> None:
    client, _ = signed_in
    client.cookies.clear()

    r = client.request(
        method, path, content=IMAGE, headers={"Authorization": f"Bearer {ADMIN_KEY}", **H}
    )

    assert r.status_code == 401
    assert queue.count_active(kind=RunKind.OCR) == 0
