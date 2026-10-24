"""The synchronous extraction routes answer with the caller's tenant's adapter."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.provider_store import InMemoryProviderStore
from tests.support.console import ADMIN_KEY, PLAIN_KEY, Ring

from docdoc.api.app import _Deployment, build_app
from docdoc.extraction import SchemaRegistry

SOURCE = Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes()


def test_v1_extract_uses_callers_tenant_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "DOCDOC_MODEL_ADAPTERS"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("DOCDOC_ECHO_FIXTURES", "tests/fixtures/echo")
    providers = InMemoryProviderStore()
    providers.set_priority("acme", ("echo",), by="alice", now=datetime(2026, 10, 23, tzinfo=UTC))
    client = TestClient(
        build_app(
            _Deployment(
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                keys=Ring(),
                providers=providers,
            )
        )
    )

    def extract(key: str) -> int:
        return client.post(
            "/v1/extract",
            params={"schema": "invoice@1"},
            content=SOURCE,
            headers={"authorization": f"Bearer {key}"},
        ).status_code

    # acme named echo; ops saved nothing, and with no env key nothing answers.
    assert extract(PLAIN_KEY) == 200
    assert extract(ADMIN_KEY) == 502
