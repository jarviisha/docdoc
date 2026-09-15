"""T031 — a credential reaches no log line, error body, or response.

**Half of SC-005, and the half a Python test can see.** The other half is the
browser's: that no request the console constructs carries the key in a URL. That
is asserted in `ui/test/console/client.test.ts`, over every intent the console
has, because a Python test cannot observe what a browser sends — and splitting
the claim is what makes both halves checkable rather than one of them assumed.

The credential here is seeded with a distinctive string, so a leak is found by
searching rather than by knowing where to look.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.runs.principal import digest_of

#: Distinctive enough that a substring search cannot miss it and nothing else
#: could produce it.
KEY = "sk-live-CANARY-4d1f9c7e"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps({"keys": [{"sha256": digest_of(KEY), "tenant_id": "acme"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))
    return TestClient(build_app(_Deployment(store_root=tmp_path)))


def test_an_accepted_credential_reaches_no_log_line(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        client.get("/v1/schemas", headers={"Authorization": f"Bearer {KEY}"})

    assert KEY not in caplog.text
    # The digest is not a leak, but a *prefix* of the key would be. Checked
    # separately because a truncating logger is the plausible way this breaks.
    assert KEY[:10] not in caplog.text


def test_a_refused_credential_reaches_no_log_line_either(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    """The failure path is the one that logs, and the one that is forgotten."""
    with caplog.at_level(logging.DEBUG):
        client.get("/v1/schemas", headers={"Authorization": "Bearer sk-live-CANARY-wrong"})

    assert "CANARY" not in caplog.text


def test_no_response_body_echoes_the_credential(client: TestClient) -> None:
    for path in ("/v1/schemas", "/v1/runs", "/v1/admin/credentials"):
        response = client.get(path, headers={"Authorization": f"Bearer {KEY}"})
        assert KEY not in response.text
        assert "CANARY" not in response.text


def test_a_refusal_does_not_quote_what_was_sent(client: TestClient) -> None:
    """An error that repeats the rejected key puts it in every client's log."""
    response = client.get("/v1/schemas", headers={"Authorization": "Bearer sk-live-CANARY-wrong"})

    assert response.status_code == 401
    assert "CANARY" not in response.text


def test_no_response_sets_a_cookie(client: TestClient) -> None:
    """FR-009: there is no session, so nothing has one to set."""
    response = client.get("/v1/schemas", headers={"Authorization": f"Bearer {KEY}"})

    assert "set-cookie" not in {header.lower() for header in response.headers}
