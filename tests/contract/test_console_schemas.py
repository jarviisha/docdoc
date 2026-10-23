"""`/v1/console/schemas`: list, read and create schemas authored in the console."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue
from tests.fixtures.schema_store import InMemorySchemaStore
from tests.support.console import H, Ring, accounts_with_alice, sign_in

from docdoc.api.app import _Deployment, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters import EchoAdapter

URL = "/v1/console/schemas"


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


@pytest.fixture
def signed_in(tmp_path: Path) -> tuple[TestClient, dict[str, str]]:
    client = _client(tmp_path, schemas=InMemorySchemaStore())
    return client, sign_in(client)


def _invoice() -> dict:
    return json.loads(Path("schemas/invoice@2.json").read_text())


def _post(client, cookie, document=None, *, prompt="Extract the fields.", base="invoice@2"):
    body = {"document": document or _invoice(), "prompt": prompt, "base": base}
    return client.post(URL, json=body, headers={**cookie, **H})


def _identities(client, cookie) -> list[str]:
    return [e["identity"] for e in client.get(URL, headers=cookie).json()["entries"]]


def test_list_has_sources(signed_in) -> None:
    client, cookie = signed_in
    entries = {e["identity"]: e for e in client.get(URL, headers=cookie).json()["entries"]}
    assert entries["invoice@1"]["source"] == "file"
    assert entries["invoice@1"]["field_count"] > 0
    assert entries["invoice@1"]["created_by"] is None


def test_create_from_base_gets_next_version(signed_in) -> None:
    client, cookie = signed_in
    r = _post(client, cookie)
    assert r.status_code == 201
    body = r.json()
    assert body["identity"] == "invoice@3"
    assert body["created_by"] == "alice"
    assert body["source"] == "console"
    assert body["base"] == "invoice@2"
    assert client.get(f"{URL}/invoice@3", headers=cookie).json()["document"]["version"] == 3
    assert "invoice@3" in _identities(client, cookie)


def test_create_twice_from_same_base(signed_in) -> None:
    client, cookie = signed_in
    _post(client, cookie)
    assert _post(client, cookie).json()["identity"] == "invoice@4"


def test_new_name_starts_at_one(signed_in) -> None:
    client, cookie = signed_in
    doc = {**_invoice(), "name": "brand_new"}
    r = _post(client, cookie, doc, base=None)
    assert r.status_code == 201
    assert r.json()["identity"] == "brand_new@1"


def test_new_name_taken_is_409(signed_in) -> None:
    client, cookie = signed_in
    r = _post(client, cookie, base=None)
    assert r.status_code == 409
    assert r.json()["error"] == "schema_name_taken"


def test_base_with_another_name_is_422(signed_in) -> None:
    client, cookie = signed_in
    r = _post(client, cookie, {**_invoice(), "name": "other"})
    assert r.status_code == 422
    assert r.json()["error"] == "invalid_schema"


def test_create_rejects_invalid_schema_with_paths(signed_in) -> None:
    client, cookie = signed_in
    doc = {
        "name": "g",
        "fields": [
            {
                "name": "rows",
                "cardinality": "repeating_group",
                "fields": [{"name": "n", "type": "integer", "constraints": {"pattern": "a"}}],
            }
        ],
    }
    r = _post(client, cookie, doc, base=None)
    assert r.status_code == 422
    problems = r.json()["problems"]
    assert problems
    assert problems[0]["path"] == "rows.n"
    assert _identities(client, cookie).count("g@1") == 0


def test_create_rejects_blank_prompt(signed_in) -> None:
    client, cookie = signed_in
    before = _identities(client, cookie)
    assert _post(client, cookie, prompt="  ").status_code == 422
    assert _identities(client, cookie) == before


def test_detail_of_a_file_schema(signed_in) -> None:
    client, cookie = signed_in
    body = client.get(f"{URL}/invoice@1", headers=cookie).json()
    assert body["source"] == "file"
    assert body["document"]["name"] == "invoice"
    assert body["prompt"]


def test_detail_unknown_is_404(signed_in) -> None:
    client, cookie = signed_in
    assert client.get(f"{URL}/nope@1", headers=cookie).status_code == 404


def test_create_without_a_store_is_503(tmp_path: Path) -> None:
    client = _client(tmp_path)
    assert _post(client, sign_in(client)).status_code == 503


def test_schema_routes_need_a_session(signed_in) -> None:
    client, _ = signed_in
    client.cookies.clear()
    assert client.get(URL).status_code == 401
    assert client.get(f"{URL}/invoice@1").status_code == 401
    assert client.post(URL, json={}, headers=H).status_code == 401
