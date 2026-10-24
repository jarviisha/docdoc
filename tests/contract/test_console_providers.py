"""`/v1/console/providers`: read and configure which model adapter answers."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from tests.fixtures.provider_store import InMemoryProviderStore
from tests.fixtures.run_queue import InMemoryRunQueue
from tests.support.console import H, Ring, accounts_with_alice, sign_in

from docdoc.api.app import _Deployment, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapter import Availability
from docdoc.extraction.adapters import EchoAdapter
from docdoc.extraction.adapters.gemini import GeminiAdapter

URL = "/v1/console/providers"
KEY = "AIzaFAKE-test-key-1234wxyz"


@pytest.fixture(autouse=True)
def _no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "DOCDOC_SECRETS_KEY",
        "DOCDOC_MODEL_ADAPTERS",
        "DOCDOC_GEMINI_MODEL",
        "GEMINI_API_KEY",
        "GOOGLE_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def _client(tmp_path: Path, **extra: object) -> TestClient:
    return TestClient(
        build_app(
            _Deployment(
                store=FileArtifactStore(tmp_path),
                blobs=BlobStore(tmp_path),
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                runs=InMemoryRunQueue(),
                console=accounts_with_alice(),
                keys=Ring(),
                **extra,  # type: ignore[arg-type]
            )
        )
    )


def _signed_in(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    client = _client(tmp_path, providers=InMemoryProviderStore())
    return client, {**sign_in(client), **H}


@pytest.fixture
def signed_in(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    return _signed_in(tmp_path)


@pytest.fixture
def with_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("DOCDOC_SECRETS_KEY", Fernet.generate_key().decode())
    return _signed_in(tmp_path)


def _adapter(body: dict, adapter_id: str) -> dict:
    return next(a for a in body["adapters"] if a["id"] == adapter_id)


def test_get_default_with_nothing_saved(signed_in) -> None:
    client, cookie = signed_in
    r = client.get(URL, headers=cookie)
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] is None
    assert body["secrets_configured"] is False
    assert body["priority"] == {"value": ["gemini"], "source": "shipped"}
    gemini = _adapter(body, "gemini")
    assert gemini["test_only"] is False
    assert gemini["model"]["source"] == "shipped"
    assert gemini["key"] == {"last4": None, "source": "env"}
    assert gemini["status"]["usable"] is False
    assert gemini["updated_at"] is None
    assert _adapter(body, "echo")["test_only"] is True
    assert body["selected"] is None


def test_put_model_then_get_shows_tenant_source(signed_in) -> None:
    client, cookie = signed_in
    r = client.put(f"{URL}/gemini?tenant=acme", json={"model": "  gemini-x  "}, headers=cookie)
    assert r.status_code == 200
    gemini = _adapter(r.json(), "gemini")
    assert gemini["model"] == {"value": "gemini-x", "source": "tenant"}
    assert gemini["updated_by"] == "alice"
    after = client.get(f"{URL}?tenant=acme", headers=cookie).json()
    assert after["scope"] == "acme"
    assert _adapter(after, "gemini")["model"]["source"] == "tenant"
    default = client.get(URL, headers=cookie).json()
    assert _adapter(default, "gemini")["model"]["source"] == "shipped"


def test_put_model_validation(signed_in) -> None:
    client, cookie = signed_in
    assert client.put(f"{URL}/gemini", json={"model": "  "}, headers=cookie).status_code == 422
    assert client.put(f"{URL}/gemini", json={"api_key": None}, headers=cookie).status_code == 422
    r = client.put(f"{URL}/gemini", json={"model": "m" * 257}, headers=cookie)
    assert r.status_code == 422
    assert client.put(f"{URL}/echo", json={"model": None}, headers=cookie).status_code == 404
    assert client.put(f"{URL}/nope", json={"model": None}, headers=cookie).status_code == 404
    r = client.put(f"{URL}/gemini?tenant=Bad!", json={"model": None}, headers=cookie)
    assert r.status_code == 422


def test_put_key_without_secrets_is_409(signed_in) -> None:
    client, cookie = signed_in
    r = client.put(f"{URL}/gemini", json={"model": None, "api_key": KEY}, headers=cookie)
    assert r.status_code == 409
    assert r.json()["error"] == "secrets_not_configured"
    assert KEY not in r.text


def test_put_key_with_secrets_shows_last4_only(with_secrets) -> None:
    client, cookie = with_secrets
    r = client.put(f"{URL}/gemini", json={"model": None, "api_key": KEY}, headers=cookie)
    assert r.status_code == 200
    assert KEY not in r.text
    body = r.json()
    assert body["secrets_configured"] is True
    assert _adapter(body, "gemini")["key"] == {"last4": KEY[-4:], "source": "default"}
    assert KEY not in client.get(URL, headers=cookie).text
    # Absent keeps it, null clears it.
    kept = client.put(f"{URL}/gemini", json={"model": "m"}, headers=cookie).json()
    assert _adapter(kept, "gemini")["model"] == {"value": "m", "source": "default"}
    assert _adapter(kept, "gemini")["key"]["last4"] == KEY[-4:]
    cleared = client.put(f"{URL}/gemini", json={"model": "m", "api_key": None}, headers=cookie)
    assert _adapter(cleared.json(), "gemini")["key"] == {"last4": None, "source": "env"}


def test_a_bad_body_never_echoes_the_key(with_secrets) -> None:
    client, cookie = with_secrets
    for body in ({"model": 5, "api_key": KEY}, {"model": None, "api_key": [KEY]}):
        r = client.put(f"{URL}/gemini", json=body, headers=cookie)
        assert r.status_code == 422
        assert KEY not in r.text
    r = client.post(f"{URL}/gemini/test", json={"model": 5, "api_key": KEY}, headers=cookie)
    assert r.status_code == 422
    assert KEY not in r.text


def test_every_provider_refusal_carries_a_message(signed_in) -> None:
    client, cookie = signed_in
    bad_json = client.put(f"{URL}/priority", content=b"{nope", headers=cookie)
    bad_tenant = client.put(f"{URL}/gemini?tenant=a%20b", json={"model": "m"}, headers=cookie)
    no_model = client.put(f"{URL}/gemini", json={}, headers=cookie)
    for r, error in (
        (bad_json, "invalid_json"),
        (bad_tenant, "invalid_tenant_id"),
        (no_model, "invalid_model"),
    ):
        assert r.status_code == 422
        body = r.json()
        body = body.get("detail", body)
        assert body["error"] == error
        assert body["message"]


def test_priority_rejects_unknown_and_empty(signed_in) -> None:
    client, cookie = signed_in
    assert client.put(f"{URL}/priority", json={}, headers=cookie).status_code == 422
    for value in (["nope"], [], ["gemini", "gemini"], "gemini", [["gemini"]]):
        r = client.put(f"{URL}/priority", json={"priority": value}, headers=cookie)
        assert r.status_code == 422, value
        assert r.json()["error"] == "invalid_priority"
        assert r.json()["message"]
    r = client.put(f"{URL}/priority?tenant=acme", json={"priority": ["echo"]}, headers=cookie)
    assert r.status_code == 200
    assert r.json()["priority"] == {"value": ["echo"], "source": "tenant"}
    assert r.json()["selected"] == "echo"
    r = client.put(f"{URL}/priority?tenant=acme", json={"priority": None}, headers=cookie)
    assert r.json()["priority"]["source"] == "shipped"


def test_reset_tenant_and_reset_default_is_422(signed_in) -> None:
    client, cookie = signed_in
    client.put(f"{URL}/gemini?tenant=acme", json={"model": "gemini-x"}, headers=cookie)
    refused = client.delete(URL, headers=cookie)
    assert refused.status_code == 422
    assert refused.json()["error"] == "tenant_required"
    assert refused.json()["message"]
    r = client.delete(f"{URL}?tenant=acme", headers=cookie)
    assert r.status_code == 200
    assert _adapter(r.json(), "gemini")["model"]["source"] == "shipped"


def test_probe_error_is_redacted(signed_in, monkeypatch: pytest.MonkeyPatch) -> None:
    client, cookie = signed_in
    seen: list[str | None] = []

    def probe(self: GeminiAdapter, timeout_s: float = 10.0) -> Availability:
        seen.append(self.model_id)
        return Availability(usable=False, reason=f"key {self._api_key} rejected; {KEY}")

    monkeypatch.setattr(GeminiAdapter, "probe", probe)
    r = client.post(
        f"{URL}/gemini/test", json={"model": "gemini-y", "api_key": KEY}, headers=cookie
    )
    assert r.status_code == 200
    assert r.json()["ok"] is False
    assert "••••" in r.json()["message"]
    assert KEY not in r.text
    assert seen == ["gemini-y"]


def _probe_echoing_the_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def probe(self: GeminiAdapter, timeout_s: float = 10.0) -> Availability:
        return Availability(usable=False, reason=f"key {self._resolved_key()} rejected")

    monkeypatch.setattr(GeminiAdapter, "probe", probe)


def test_probe_redacts_a_saved_key(with_secrets, monkeypatch: pytest.MonkeyPatch) -> None:
    client, cookie = with_secrets
    client.put(f"{URL}/gemini", json={"model": None, "api_key": KEY}, headers=cookie)
    _probe_echoing_the_key(monkeypatch)
    r = client.post(f"{URL}/gemini/test", json={}, headers=cookie)
    assert r.json() == {"ok": False, "message": "key •••• rejected"}


def test_probe_redacts_the_env_key(signed_in, monkeypatch: pytest.MonkeyPatch) -> None:
    client, cookie = signed_in
    monkeypatch.setenv("GEMINI_API_KEY", KEY)
    _probe_echoing_the_key(monkeypatch)
    r = client.post(f"{URL}/gemini/test", json={}, headers=cookie)
    assert r.json() == {"ok": False, "message": "key •••• rejected"}


def test_an_oversized_body_is_refused_unread(signed_in) -> None:
    client, cookie = signed_in
    r = client.put(f"{URL}/gemini", json={"model": None, "api_key": KEY * 1000}, headers=cookie)
    assert r.status_code == 413
    assert KEY not in r.text


def test_echo_test_answers_with_available(signed_in) -> None:
    client, cookie = signed_in
    r = client.post(f"{URL}/echo/test", json={}, headers=cookie)
    assert r.status_code == 200
    assert r.json()["ok"] is True


def test_provider_routes_need_a_session(tmp_path: Path) -> None:
    client = _client(tmp_path, providers=InMemoryProviderStore())
    assert client.get(URL).status_code == 401
    assert client.put(f"{URL}/priority", json={"priority": None}, headers=H).status_code == 401
    assert client.put(f"{URL}/gemini", json={"model": None}, headers=H).status_code == 401
    assert client.delete(f"{URL}?tenant=acme", headers=H).status_code == 401
    assert client.post(f"{URL}/gemini/test", json={}, headers=H).status_code == 401


def test_without_a_provider_store_is_503(tmp_path: Path) -> None:
    client = _client(tmp_path)
    cookie = {**sign_in(client), **H}
    assert client.get(URL, headers=cookie).status_code == 503
    assert client.put(f"{URL}/gemini", json={"model": None}, headers=cookie).status_code == 503
