"""The console's callback routes, per tenant (ADR-0018, ADR-0023 §3, 021 Task 1).

`/v1/console/tenants/{tenant_id}/callbacks*` answers only to a console session
and reuses `PostgresDeliverer.register`/`revoke`/`callbacks_for` -- the same
destination policy, the same 422s, and the same `_delivery_unconfigured` 503
`/v1/callbacks` already answers with. The one capability it adds over that
tenant surface is the listing FR-066 forbids there: a console operator sees a
tenant's destinations, and still never the secret or its digest.

Against a real database, as `test_review_routes_http.py` is: `deployment.
deliverer()` returns something only for a queue with its own `_execute`, which
an in-memory double has none of.
"""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

import pytest
from tests.infra import require_database

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.support.console import ADMIN_KEY, H, Ring, accounts_with_alice, sign_in

from docdoc.api.app import _Deployment, build_app
from docdoc.runs import migrations
from docdoc.runs.identity import DELIVERY_SECRETS_FILE_ENV
from docdoc.runs.postgres import PostgresRunQueue

pytestmark = pytest.mark.postgres

ACME = "acme"
GLOBEX = "globex"
SECRET = "whsec_console-admin-screens-test-secret"
#: `localhost`, because registration *resolves* the destination and this is the
#: same pattern `test_review_routes_http.py`'s own `CALLBACK_URL` is: nothing
#: is ever connected to (FR-064), so a loopback address is fine once the SSRF
#: policy is relaxed for the test.
CALLBACK_URL = "https://localhost:9999/hook"


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs, deliveries, callbacks")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


def _build(tmp_path: Path, queue: PostgresRunQueue) -> _Deployment:
    return _Deployment(store_root=tmp_path, runs=queue, console=accounts_with_alice(), keys=Ring())


@pytest.fixture
def client(tmp_path: Path, queue: PostgresRunQueue, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """A signing secret configured and the SSRF policy relaxed.

    Relaxed for the same reason `test_review_routes_http.py`'s fixture relaxes
    it: every hostname a test can name resolves privately or not at all.
    `test_console_register_refuses_a_private_destination` turns it back off.
    """
    secrets = tmp_path / "delivery-secrets.json"
    secrets.write_text(json.dumps({"secrets": [SECRET]}), encoding="utf-8")
    monkeypatch.setenv(DELIVERY_SECRETS_FILE_ENV, str(secrets))
    monkeypatch.setenv("DOCDOC_DELIVERY_ALLOW_PRIVATE", "1")
    return TestClient(build_app(_build(tmp_path, queue)))


@pytest.fixture
def cookie(client: TestClient) -> dict[str, str]:
    return sign_in(client)


def _register(
    client: TestClient,
    cookie: dict[str, str],
    *,
    tenant: str = ACME,
    url: str = CALLBACK_URL,
    secret: str = SECRET,
) -> dict:
    response = client.post(
        f"/v1/console/tenants/{tenant}/callbacks",
        json={"url": url, "secret": secret},
        headers={**cookie, **H},
    )
    assert response.status_code == 201, response.text
    return response.json()


def _list(client: TestClient, cookie: dict[str, str], tenant: str = ACME) -> list[dict]:
    response = client.get(f"/v1/console/tenants/{tenant}/callbacks", headers=cookie)
    assert response.status_code == 200, response.text
    return response.json()["callbacks"]


def test_console_lists_a_tenants_active_callbacks_newest_first(
    client: TestClient, cookie: dict[str, str]
) -> None:
    """Newest first, active only, and never the secret or its digest."""
    first = _register(client, cookie, url="https://localhost:9991/one")
    second = _register(client, cookie, url="https://localhost:9992/two")
    third = _register(client, cookie, url="https://localhost:9993/three")

    revoked = client.delete(
        f"/v1/console/tenants/{ACME}/callbacks/{first['callback_id']}", headers={**cookie, **H}
    )
    assert revoked.status_code == 204

    response = client.get(f"/v1/console/tenants/{ACME}/callbacks", headers=cookie)
    body = response.json()

    assert [row["callback_id"] for row in body["callbacks"]] == [
        third["callback_id"],
        second["callback_id"],
    ]
    for row in body["callbacks"]:
        assert set(row) == {"callback_id", "tenant_id", "url", "created_at"}
        assert row["tenant_id"] == ACME
    assert "secret" not in response.text
    assert "secret_digest" not in response.text


def test_console_registers_a_callback(
    client: TestClient, cookie: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """`201` with `tenant_id`, visible in the list, logged without the secret."""
    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        body = _register(client, cookie)

    assert body["url"] == CALLBACK_URL
    assert body["tenant_id"] == ACME
    assert body["callback_id"]
    assert "secret" not in caplog.text

    listed = _list(client, cookie)
    assert [row["callback_id"] for row in listed] == [body["callback_id"]]

    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "docdoc.api" and "console.callback_register" in record.getMessage()
    ]
    assert events == [
        {
            "event": "console.callback_register",
            "actor": "console:alice",
            "tenant_id": ACME,
            "callback_id": body["callback_id"],
            "url": CALLBACK_URL,
            "status": 201,
        }
    ]


def test_console_register_without_a_url_or_a_secret_names_the_error_exactly(
    client: TestClient, cookie: dict[str, str]
) -> None:
    """The same body `POST /v1/callbacks` answers with (021 Task 1 review finding 2).

    `_register_callback` is shared, so this pins that the console's answer did
    not drift from the tenant route's own
    `test_a_callback_without_a_url_or_a_secret_names_the_error_exactly`.
    """
    for body in ({"secret": SECRET}, {"url": CALLBACK_URL}, {}):
        response = client.post(
            f"/v1/console/tenants/{ACME}/callbacks", json=body, headers={**cookie, **H}
        )

        assert response.status_code == 422, body
        assert response.json() == {
            "detail": {"error": "invalid_callback", "detail": "url and secret are required"}
        }
    assert _list(client, cookie) == []


def test_console_register_refuses_an_unknown_secret(
    client: TestClient, cookie: dict[str, str]
) -> None:
    """A secret the deployment never configured is a refusal, and stores nothing."""
    response = client.post(
        f"/v1/console/tenants/{ACME}/callbacks",
        json={"url": CALLBACK_URL, "secret": "whsec_never-configured-anywhere"},
        headers={**cookie, **H},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "destination_refused"
    assert _list(client, cookie) == []


def test_console_register_refuses_a_private_destination(
    client: TestClient, cookie: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-060, with the fixture's relaxation turned back off for this one case.

    The deliverer is built once and cached, so this reaches into it rather than
    rebuilding the app -- `test_review_routes_http.py`'s own pattern for the
    tenant route this one mirrors.
    """
    deployment: _Deployment = client.app.state.deployment  # type: ignore[attr-defined]
    monkeypatch.setattr(deployment.deliverer(), "allow_private", False)

    response = client.post(
        f"/v1/console/tenants/{ACME}/callbacks",
        json={"url": "http://127.0.0.1/hook", "secret": SECRET},
        headers={**cookie, **H},
    )

    assert response.status_code == 422
    assert response.json()["error"] == "destination_refused"
    assert _list(client, cookie) == []


def test_console_revoke_is_idempotent(
    client: TestClient, cookie: dict[str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """`204` twice, gone from the list, and exactly one logged revoke."""
    registered = _register(client, cookie)

    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        first = client.delete(
            f"/v1/console/tenants/{ACME}/callbacks/{registered['callback_id']}",
            headers={**cookie, **H},
        )
        second = client.delete(
            f"/v1/console/tenants/{ACME}/callbacks/{registered['callback_id']}",
            headers={**cookie, **H},
        )

    assert first.status_code == second.status_code == 204
    assert _list(client, cookie) == []

    events = [
        json.loads(record.getMessage())
        for record in caplog.records
        if record.name == "docdoc.api" and "console.callback_revoke" in record.getMessage()
    ]
    assert events == [
        {
            "event": "console.callback_revoke",
            "actor": "console:alice",
            "tenant_id": ACME,
            "callback_id": registered["callback_id"],
            "status": 204,
        }
    ]


def test_console_callbacks_without_delivery_is_503(
    tmp_path: Path, queue: PostgresRunQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same answer `POST /v1/callbacks` gives a deployment with nothing to sign with."""
    monkeypatch.delenv(DELIVERY_SECRETS_FILE_ENV, raising=False)
    client = TestClient(build_app(_build(tmp_path, queue)))
    cookie = sign_in(client)

    listed = client.get(f"/v1/console/tenants/{ACME}/callbacks", headers=cookie)
    registered = client.post(
        f"/v1/console/tenants/{ACME}/callbacks",
        json={"url": CALLBACK_URL, "secret": SECRET},
        headers={**cookie, **H},
    )

    for response in (listed, registered):
        assert response.status_code == 503, response.text
        assert response.json()["error"]["class"] == "DeliveryError"


def test_console_callback_routes_refuse_a_bearer_key(
    client: TestClient, cookie: dict[str, str]
) -> None:
    """A Bearer key -- even an admin-scoped one -- opens none of this (ADR-0023 §3)."""
    registered = _register(client, cookie)
    client.cookies.clear()
    bearer = {"Authorization": f"Bearer {ADMIN_KEY}"}

    get_response = client.get(f"/v1/console/tenants/{ACME}/callbacks", headers=bearer)
    post_response = client.post(
        f"/v1/console/tenants/{ACME}/callbacks",
        json={"url": CALLBACK_URL, "secret": SECRET},
        headers={**bearer, **H},
    )
    delete_response = client.delete(
        f"/v1/console/tenants/{ACME}/callbacks/{registered['callback_id']}",
        headers={**bearer, **H},
    )

    for response in (get_response, post_response, delete_response):
        assert response.status_code == 401, response.text
        assert response.json()["error"]["class"] == "ConsoleSessionRequired"


def test_console_callback_writes_need_the_console_header(
    client: TestClient, cookie: dict[str, str]
) -> None:
    """A write without `X-Docdoc-Console: 1` is a 403, before the session matters."""
    post_response = client.post(
        f"/v1/console/tenants/{ACME}/callbacks",
        json={"url": CALLBACK_URL, "secret": SECRET},
        headers=cookie,
    )
    delete_response = client.delete(
        f"/v1/console/tenants/{ACME}/callbacks/00000000-0000-4000-8000-000000000000",
        headers=cookie,
    )

    for response in (post_response, delete_response):
        assert response.status_code == 403, response.text
        assert response.json()["error"]["class"] == "ConsoleHeaderMissing"
    assert _list(client, cookie) == []


def test_console_callbacks_are_tenant_scoped(client: TestClient, cookie: dict[str, str]) -> None:
    """Another tenant's listing is empty, and a cross-tenant revoke is a no-op."""
    registered = _register(client, cookie, tenant=ACME)

    assert _list(client, cookie, tenant=GLOBEX) == []
    assert [row["callback_id"] for row in _list(client, cookie, tenant=ACME)] == [
        registered["callback_id"]
    ]

    cross_tenant_delete = client.delete(
        f"/v1/console/tenants/{GLOBEX}/callbacks/{registered['callback_id']}",
        headers={**cookie, **H},
    )
    assert cross_tenant_delete.status_code == 204

    assert [row["callback_id"] for row in _list(client, cookie, tenant=ACME)] == [
        registered["callback_id"]
    ]


def test_an_invalid_tenant_id_is_a_422_on_every_route(
    client: TestClient, cookie: dict[str, str]
) -> None:
    bogus = "Not-Valid"
    get_response = client.get(f"/v1/console/tenants/{bogus}/callbacks", headers=cookie)
    post_response = client.post(
        f"/v1/console/tenants/{bogus}/callbacks",
        json={"url": CALLBACK_URL, "secret": SECRET},
        headers={**cookie, **H},
    )
    delete_response = client.delete(
        f"/v1/console/tenants/{bogus}/callbacks/00000000-0000-4000-8000-000000000000",
        headers={**cookie, **H},
    )

    for response in (get_response, post_response, delete_response):
        assert response.status_code == 422, response.text
        assert response.json() == {"error": "invalid_tenant_id"}
