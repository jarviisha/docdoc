"""T180, FR-035 — one structured event per credential operation.

An audit trail is the thing an incident review reads, so its failure mode is
particular: nobody notices it is missing until the moment somebody needs it, and
by then the operations it should have recorded are months gone.

`_audit` has existed since Phase 5 and was asserted nowhere. It appeared in the
suite exactly once — in `test_telemetry_leaks_nothing.py`, as an example of an
event the span bridge deliberately **drops** — which is a test that the event is
not exported, not a test that it is emitted.

**And the event must not record what it is auditing.** An audit trail that
carried the credentials it describes would be the single richest place to steal
from in the deployment: one grep, every key ever issued.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.runs.keys import Credential
from docdoc.runs.principal import digest_of

EVENT = "credential.operation"
ADMIN_KEY = "admin-key-for-the-audit-test"

#: Seeded so that a leak is unambiguous rather than a coincidence.
ISSUED_SECRET = "ddk_a_distinctive_issued_credential_for_this_test"


class _Store:
    """A key store that issues a known secret, so a leak has a needle to find."""

    def __init__(self) -> None:
        self.revoked: list[UUID] = []

    def resolve(self, digest: str):
        return None

    def issue(self, *, tenant_id: str, scopes, label, now):
        return ISSUED_SECRET, Credential(
            credential_id=uuid4(),
            tenant_id=tenant_id,
            scopes=scopes,
            label=label,
            created_at=now,
        )

    def revoke(self, credential_id: UUID, *, now) -> bool:
        self.revoked.append(credential_id)
        return True

    def list_for(self, tenant_id: str) -> tuple[Credential, ...]:
        return ()


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """One administrative credential, from the file ring, and a store to mutate."""
    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps({"keys": [{"sha256": digest_of(ADMIN_KEY), "tenant_id": "ops"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))

    from docdoc.api.auth import KeyRing
    from docdoc.runs.principal import ADMIN_SCOPE, Principal

    class _AdminRing(KeyRing):
        """The file ring, with the one scope an admin route needs.

        A file ring grants no scopes by design (FR-038) — that is what stops an
        upgrade handing anybody administrative access — so a test of the admin
        routes has to supply one deliberately. Subclassing rather than seeding a
        table keeps this a unit test of the event, not of the key store.
        """

        def resolve(self, digest: str) -> Principal | None:
            found = super().resolve(digest)
            return None if found is None else Principal(found.tenant_id, {ADMIN_SCOPE})

        def principal_for(self, credential: str | None) -> Principal:
            found = super().principal_for(credential)
            return Principal(found.tenant_id, {ADMIN_SCOPE})

    ring = _AdminRing.from_file(keys)
    return TestClient(build_app(_Deployment(keys=ring, runs=_Runs())))


class _Runs:
    """Just enough for `_Deployment` to build a chained key store around `_Store`."""

    _execute = staticmethod(lambda *a, **k: None)


def _events(caplog: pytest.LogCaptureFixture) -> list[dict]:
    return [
        record.docdoc  # type: ignore[attr-defined]
        for record in caplog.records
        if getattr(record, "docdoc", {}).get("event") == EVENT
    ]


def _issue(client: TestClient):
    return client.post(
        "/v1/admin/credentials",
        headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        json={"tenant_id": "acme", "label": "ci"},
    )


def test_issuing_emits_one_event(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**FR-035.** One event, and exactly one."""
    _install(client, monkeypatch)

    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        response = _issue(client)

    assert response.status_code == 201, response.text
    events = _events(caplog)
    assert len(events) == 1
    assert events[0]["operation"] == "issue"


def test_the_event_names_actor_operation_credential_and_tenant(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The four facts an incident review needs to answer "who did what, to which"."""
    _install(client, monkeypatch)

    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        _issue(client)

    event = _events(caplog)[0]
    assert event["actor"] == "ops"
    assert event["operation"] == "issue"
    assert event["tenant_id"] == "acme"
    assert UUID(event["credential_id"])


def test_the_event_never_carries_the_credential(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**The assertion that matters.**

    The response body carries the plaintext once, by design. The audit line must
    not — an audit trail that recorded what it was auditing would be the single
    richest place to steal from in the deployment.
    """
    _install(client, monkeypatch)

    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        response = _issue(client)

    assert ISSUED_SECRET in response.text, "the response is the one place it appears"

    for event in _events(caplog):
        assert ISSUED_SECRET not in json.dumps(event)
    for record in caplog.records:
        assert ISSUED_SECRET not in record.getMessage()


def test_revoking_emits_its_own_event(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Revocation is the operation an incident review is usually looking for."""
    _install(client, monkeypatch)
    identity = uuid4()

    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        response = client.delete(
            f"/v1/admin/credentials/{identity}",
            headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        )

    assert response.status_code == 204
    events = _events(caplog)
    assert len(events) == 1
    assert events[0]["operation"] == "revoke"
    assert events[0]["credential_id"] == str(identity)
    assert events[0]["actor"] == "ops"


def test_a_listing_emits_nothing(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reading is not an operation on a credential.

    An event per read would bury the two that change something under however
    many times an operator refreshed a page.
    """
    _install(client, monkeypatch)

    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        client.get(
            "/v1/admin/credentials?tenant_id=acme",
            headers={"Authorization": f"Bearer {ADMIN_KEY}"},
        )

    assert _events(caplog) == []


def _install(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the deployment's chained key store at the fake that issues a known secret."""
    from docdoc.runs.keys import ChainedKeyStore

    deployment = client.app.state.deployment  # type: ignore[attr-defined]
    monkeypatch.setattr(deployment, "keys", ChainedKeyStore(ring=deployment.ring, store=_Store()))


def test_this_check_can_actually_fail(
    client: TestClient, caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the guard: a capture that saw nothing would pass every assertion
    of the form "no event contains the secret"."""
    _install(client, monkeypatch)

    with caplog.at_level(logging.INFO, logger="docdoc.api"):
        _issue(client)

    assert _events(caplog), "no audit event was captured, so the sweeps above are vacuous"
