"""Console sessions behind their own gate (ADR-0023 §1, §3).

`/v1/console` is a separate route family: a console session opens it and
nothing else does — not a Bearer key, admin-scoped or otherwise, and not
authentication being off. Equally, a console session opens nothing outside it.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import ClassVar

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.console_accounts import InMemoryConsoleAccounts
from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.api.app import _Deployment, build_app
from docdoc.runs.principal import ADMIN_SCOPE, Principal, digest_of

H = {"X-Docdoc-Console": "1"}
ADMIN_KEY = "an-admin-key"
PLAIN_KEY = "a-plain-key"


class _Ring:
    """Authentication on, with one admin-scoped key and one ordinary one.

    The file `KeyRing` builds principals with no scopes, so the admin key is
    held here — enough to head a `ChainedKeyStore`, which is all `_Deployment`
    asks of a ring once authentication is on.
    """

    enabled = True
    _by_digest: ClassVar[dict[str, Principal]] = {
        digest_of(ADMIN_KEY): Principal(tenant_id="ops", scopes=frozenset({ADMIN_SCOPE})),
        digest_of(PLAIN_KEY): Principal(tenant_id="acme"),
    }

    def resolve(self, digest: str) -> Principal | None:
        return self._by_digest.get(digest)


@pytest.fixture
def accounts() -> InMemoryConsoleAccounts:
    store = InMemoryConsoleAccounts()
    store.add(
        "alice", "long enough pass", salt=b"\x01" * 16, now=datetime(2026, 10, 20, tzinfo=UTC)
    )
    return store


@pytest.fixture
def client(tmp_path: Path, accounts: InMemoryConsoleAccounts) -> TestClient:
    return TestClient(
        build_app(
            _Deployment(
                store_root=tmp_path, runs=InMemoryRunQueue(), console=accounts, keys=_Ring()
            )
        )
    )


@pytest.fixture
def signed_in(client: TestClient) -> TestClient:
    r = client.post(
        "/v1/console/session",
        json={"username": "alice", "password": "long enough pass"},
        headers=H,
    )
    assert r.status_code == 200
    return client


def test_login_sets_an_httponly_strict_cookie(client: TestClient) -> None:
    r = client.post(
        "/v1/console/session",
        json={"username": "alice", "password": "long enough pass"},
        headers=H,
    )
    assert r.status_code == 200
    assert r.json() == {"username": "alice"}
    cookie = r.headers["set-cookie"].lower()
    assert "docdoc_console=" in cookie
    assert "httponly" in cookie
    assert "samesite=strict" in cookie
    assert "path=/v1/console" in cookie
    assert client.get("/v1/console/session").json() == {"username": "alice"}


def test_login_trims_and_lowercases_the_username(client: TestClient) -> None:
    r = client.post(
        "/v1/console/session",
        json={"username": "  Alice ", "password": "long enough pass"},
        headers=H,
    )
    assert r.status_code == 200
    assert r.json() == {"username": "alice"}


def test_a_wrong_password_and_an_unknown_user_get_one_answer(client: TestClient) -> None:
    a = client.post(
        "/v1/console/session", json={"username": "alice", "password": "nope nope nope"}, headers=H
    )
    b = client.post(
        "/v1/console/session",
        json={"username": "mallory", "password": "nope nope nope"},
        headers=H,
    )
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()
    assert a.json()["error"]["class"] == "LoginRefused"
    assert "set-cookie" not in a.headers


def test_a_write_without_the_console_header_is_refused(client: TestClient) -> None:
    r = client.post(
        "/v1/console/session", json={"username": "alice", "password": "long enough pass"}
    )
    assert r.status_code == 403
    assert r.json()["error"]["class"] == "ConsoleHeaderMissing"


def test_a_bearer_key_never_opens_the_console_api(client: TestClient) -> None:
    # The admin key really is admin on the rest of `/v1`: it gets past the scope
    # check an ordinary key is refused at with a 404 (ADR-0016 §6).
    admin_route = "/v1/admin/credentials?tenant_id=acme"
    assert (
        client.get(admin_route, headers={"Authorization": f"Bearer {PLAIN_KEY}"}).status_code == 404
    )
    assert (
        client.get(admin_route, headers={"Authorization": f"Bearer {ADMIN_KEY}"}).status_code != 404
    )
    for key in (ADMIN_KEY, PLAIN_KEY, "garbage"):
        r = client.get("/v1/console/session", headers={"Authorization": f"Bearer {key}"})
        assert r.status_code == 401
        assert r.json()["error"]["class"] == "ConsoleSessionRequired"
        assert "www-authenticate" not in r.headers


def test_a_console_session_never_opens_the_rest_of_v1(signed_in: TestClient) -> None:
    assert signed_in.get("/v1/runs").status_code == 401


def test_logout_ends_the_session(signed_in: TestClient) -> None:
    r = signed_in.delete("/v1/console/session", headers=H)
    assert r.status_code == 204
    assert "max-age=0" in r.headers["set-cookie"].lower()
    assert signed_in.get("/v1/console/session").status_code == 401


def test_without_a_run_database_the_console_says_so(tmp_path: Path) -> None:
    client = TestClient(build_app(_Deployment(store_root=tmp_path)))
    r = client.post("/v1/console/session", json={"username": "a", "password": "b"}, headers=H)
    assert r.status_code == 503
    assert r.json()["error"]["class"] == "ConsoleUnavailable"


def test_the_console_needs_a_session_even_with_tenant_auth_off(
    tmp_path: Path, accounts: InMemoryConsoleAccounts
) -> None:
    client = TestClient(
        build_app(_Deployment(store_root=tmp_path, runs=InMemoryRunQueue(), console=accounts))
    )
    assert client.get("/v1/console/session").status_code == 401


def test_the_shell_is_still_open(client: TestClient) -> None:
    assert client.get("/console/").status_code in (200, 501)
