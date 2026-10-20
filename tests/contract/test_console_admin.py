"""Task 6: console credentials and erasure (ADR-0023 §2, §3).

`/v1/console/credentials*` and `/v1/console/tenants/*` mirror the admin routes
of the same shape, behind a console session rather than a Bearer key, and
audited as `console:<username>` rather than the Bearer tenant.

**§2: an admin-scoped key stays CLI-only.** The console's issue route accepts
no `scopes` field at all -- not even an empty one -- because a route that could
mint any scope is a route one compromised session away from minting the admin
scope too.
"""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue
from tests.support.console import ADMIN_KEY, Ring, accounts_with_alice, sign_in

from docdoc.api.app import _Deployment, build_app
from docdoc.runs.keys import ChainedKeyStore, Credential

H = {"X-Docdoc-Console": "1"}


class _Store:
    """A key store in memory, with the same three mutating verbs as Postgres.

    Minimal equivalent of `FakeStore` in `tests/unit/test_credential_lifecycle.py`
    (that one's `issue` deliberately raises `NotImplementedError`, since nothing
    there exercises issuance).
    """

    def __init__(self) -> None:
        self._by_id: dict[UUID, Credential] = {}

    def resolve(self, digest: str) -> None:
        return None

    def issue(
        self, *, tenant_id: str, scopes: frozenset[str], label: str | None, now: datetime
    ) -> tuple[str, Credential]:
        credential = Credential(
            credential_id=uuid4(), tenant_id=tenant_id, scopes=scopes, label=label, created_at=now
        )
        self._by_id[credential.credential_id] = credential
        return f"ddk_{credential.credential_id}", credential

    def revoke(self, credential_id: UUID, *, now: datetime) -> bool:
        found = self._by_id.get(credential_id)
        if found is None or found.revoked_at is not None:
            return False
        self._by_id[credential_id] = replace(found, revoked_at=now)
        return True

    def list_for(self, tenant_id: str) -> tuple[Credential, ...]:
        return tuple(c for c in self._by_id.values() if c.tenant_id == tenant_id)


@pytest.fixture
def queue() -> InMemoryRunQueue:
    return InMemoryRunQueue()


@pytest.fixture
def deployment(tmp_path: Path, queue: InMemoryRunQueue) -> _Deployment:
    return _Deployment(store_root=tmp_path, runs=queue, console=accounts_with_alice(), keys=Ring())


@pytest.fixture
def signed_in(
    deployment: _Deployment, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, dict[str, str]]:
    """A deployment whose key store is the in-memory fake, not Postgres."""
    monkeypatch.setattr(deployment, "keys", ChainedKeyStore(ring=deployment.ring, store=_Store()))
    client = TestClient(build_app(deployment))
    return client, sign_in(client)


def _headers(cookie: dict[str, str]) -> dict[str, str]:
    return {**cookie, **H}


def _erasure_events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    """Every `console.erasure` event, parsed from its own log line.

    Unlike `_audit`'s `credential.operation` (passed through `extra={"docdoc":
    ...}}`), `console.erasure` is logged in the "the message is the JSON"
    style `console.assets` and `runs.driver_missing` already use on
    `docdoc.api` -- the message itself is `json.dumps(...)`, so a plain
    `record.getMessage()` is parsed rather than read off `record.docdoc`.
    """
    events = []
    for record in caplog.records:
        try:
            payload = json.loads(record.getMessage())
        except ValueError:
            continue
        if isinstance(payload, dict) and payload.get("event") == "console.erasure":
            events.append(payload)
    return events


def test_issue_returns_the_key_once_and_list_omits_it(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, cookie = signed_in

    issued = client.post(
        "/v1/console/credentials",
        json={"tenant_id": "acme", "label": "ci"},
        headers=_headers(cookie),
    )

    assert issued.status_code == 201, issued.text
    body = issued.json()
    assert body["tenant_id"] == "acme"
    assert body["key"].startswith("ddk_")

    listing = client.get(
        "/v1/console/credentials", params={"tenant": "acme"}, headers=cookie
    ).json()

    assert len(listing["credentials"]) == 1
    row = listing["credentials"][0]
    assert row["credential_id"] == body["credential_id"]
    assert "key" not in row
    assert "digest" not in row


def test_a_whitespace_label_is_stored_as_none(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    """Mirrors the admin route's `label=(body.get("label") or None)`."""
    client, cookie = signed_in

    issued = client.post(
        "/v1/console/credentials",
        json={"tenant_id": "acme", "label": "   "},
        headers=_headers(cookie),
    )

    assert issued.status_code == 201, issued.text
    listing = client.get(
        "/v1/console/credentials", params={"tenant": "acme"}, headers=cookie
    ).json()
    row = next(
        r for r in listing["credentials"] if r["credential_id"] == issued.json()["credential_id"]
    )
    assert row["label"] is None


def test_issue_refuses_any_scopes_field(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, cookie = signed_in

    response = client.post(
        "/v1/console/credentials",
        json={"tenant_id": "acme", "scopes": ["admin"]},
        headers=_headers(cookie),
    )

    assert response.status_code == 422
    assert response.json() == {"error": "scopes_not_accepted"}


def test_revoke_is_idempotent(signed_in: tuple[TestClient, dict[str, str]]) -> None:
    client, cookie = signed_in
    issued = client.post(
        "/v1/console/credentials",
        json={"tenant_id": "acme"},
        headers=_headers(cookie),
    ).json()

    first = client.delete(
        f"/v1/console/credentials/{issued['credential_id']}", headers=_headers(cookie)
    )
    second = client.delete(
        f"/v1/console/credentials/{issued['credential_id']}", headers=_headers(cookie)
    )

    assert first.status_code == 204
    assert second.status_code == 204


def test_erasing_the_default_tenant_is_refused(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, cookie = signed_in

    response = client.delete("/v1/console/tenants/default", headers=_headers(cookie))

    assert response.status_code == 409
    assert response.json()["error"] == "default_tenant_erasure_refused"


def test_document_erasure_names_its_tenant(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, cookie = signed_in
    blob_id = "sha256:" + "a" * 64

    response = client.delete(
        f"/v1/console/tenants/acme/documents/{blob_id}", headers=_headers(cookie)
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["scope"] == "document"
    assert body["target"] == blob_id
    assert "runs" in body
    assert "artifacts" in body
    assert "blobs" in body


def test_tenant_erasure_logs_the_operator(
    signed_in: tuple[TestClient, dict[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """`_erase`/`retention.erase` name no actor; the console route names one."""
    client, cookie = signed_in

    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        response = client.delete("/v1/console/tenants/acme", headers=_headers(cookie))

    assert response.status_code == 200, response.text
    events = _erasure_events(caplog)
    assert len(events) == 1
    assert events[0]["actor"] == "console:alice"
    assert events[0]["tenant_id"] == "acme"
    assert events[0]["blob_id"] is None
    assert events[0]["status"] == 200


def test_document_erasure_logs_the_operator(
    signed_in: tuple[TestClient, dict[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    client, cookie = signed_in
    blob_id = "sha256:" + "a" * 64

    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        response = client.delete(
            f"/v1/console/tenants/acme/documents/{blob_id}", headers=_headers(cookie)
        )

    assert response.status_code == 200, response.text
    events = _erasure_events(caplog)
    assert len(events) == 1
    assert events[0]["actor"] == "console:alice"
    assert events[0]["tenant_id"] == "acme"
    assert events[0]["blob_id"] == blob_id
    assert events[0]["status"] == 200


def test_a_refused_erasure_is_still_attributed(
    signed_in: tuple[TestClient, dict[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    """A refused destructive attempt is still an attempt worth attributing."""
    client, cookie = signed_in

    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        response = client.delete("/v1/console/tenants/default", headers=_headers(cookie))

    assert response.status_code == 409
    events = _erasure_events(caplog)
    assert len(events) == 1
    assert events[0]["actor"] == "console:alice"
    assert events[0]["tenant_id"] == "default"
    assert events[0]["status"] == 409


def test_an_invalid_tenant_id_is_refused_on_every_tenant_route(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, cookie = signed_in
    bad = "Not Valid!"

    for response in (
        client.get("/v1/console/credentials", params={"tenant": bad}, headers=cookie),
        client.post("/v1/console/credentials", json={"tenant_id": bad}, headers=_headers(cookie)),
        client.delete(f"/v1/console/tenants/{bad}", headers=_headers(cookie)),
        client.delete(
            f"/v1/console/tenants/{bad}/documents/sha256:" + "a" * 64, headers=_headers(cookie)
        ),
    ):
        assert response.status_code == 422, response.text


def test_admin_actions_are_audited_with_the_operator(
    signed_in: tuple[TestClient, dict[str, str]], caplog: pytest.LogCaptureFixture
) -> None:
    import logging

    client, cookie = signed_in

    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        response = client.post(
            "/v1/console/credentials",
            json={"tenant_id": "acme"},
            headers=_headers(cookie),
        )

    assert response.status_code == 201, response.text
    events = [
        record.docdoc  # type: ignore[attr-defined]
        for record in caplog.records
        if getattr(record, "docdoc", {}).get("event") == "credential.operation"
    ]
    assert len(events) == 1
    assert events[0]["actor"] == "console:alice"


def test_the_console_admin_routes_refuse_a_bearer_key(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, _cookie = signed_in
    client.cookies.clear()
    bearer = {"Authorization": f"Bearer {ADMIN_KEY}"}

    responses = [
        client.get("/v1/console/credentials", params={"tenant": "acme"}, headers=bearer),
        client.post(
            "/v1/console/credentials",
            json={"tenant_id": "acme"},
            headers={**bearer, **H},
        ),
        client.delete(f"/v1/console/credentials/{uuid4()}", headers={**bearer, **H}),
        client.delete("/v1/console/tenants/acme", headers={**bearer, **H}),
        client.delete(
            "/v1/console/tenants/acme/documents/sha256:" + "a" * 64,
            headers={**bearer, **H},
        ),
    ]

    for response in responses:
        assert response.status_code == 401, response.text


def test_writes_need_the_console_header(
    signed_in: tuple[TestClient, dict[str, str]],
) -> None:
    client, cookie = signed_in

    responses = [
        client.post("/v1/console/credentials", json={"tenant_id": "acme"}, headers=cookie),
        client.delete(f"/v1/console/credentials/{uuid4()}", headers=cookie),
        client.delete("/v1/console/tenants/acme", headers=cookie),
        client.delete("/v1/console/tenants/acme/documents/sha256:" + "a" * 64, headers=cookie),
    ]

    for response in responses:
        assert response.status_code == 403, response.text
