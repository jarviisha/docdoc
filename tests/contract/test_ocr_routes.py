"""`POST /v1/ocr` and `GET /v1/parsers` -- the raw OCR gateway over HTTP.

Every registry here is built from fake-post `PaddleXParser`s: no socket is ever
opened, which is what the health-probe tests below also arrange for by
replacing `.probe` rather than letting it reach a fictitious host.

Review Focus 3's HTTP half is pinned by `test_no_secret_in_parsers_or_errors`:
an endpoint's `url` and `headers` must appear in neither `GET /v1/parsers` nor
any `POST /v1/ocr` error body -- `ParserChoice` carries no field that could
leak either, and the error model names an endpoint by `id` alone.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.support import KEYS_FILE, bearer

from docdoc.api.app import _Deployment, build_app
from docdoc.api.auth import KeyRing
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.ingest.capabilities import ParserCapabilities
from docdoc.ingest.errors import ProviderError
from docdoc.ingest.parsers.paddlex import OcrEndpoint, PaddleXParser
from docdoc.ingest.registry import ParserRegistry
from docdoc.ingest.source import JPEG, PDF, PNG

IMAGE = (Path(__file__).parent.parent / "fixtures" / "image" / "sample_page.png").read_bytes()


def _endpoint(
    parser_id: str,
    *,
    languages: tuple[str, ...] = (),
    headers: dict[str, str] | None = None,
    url: str | None = None,
) -> OcrEndpoint:
    return OcrEndpoint(
        id=parser_id,
        url=url or f"http://{parser_id}.example/ocr",
        version="1.0.0",
        languages=languages,
        headers=headers or {},
    )


def _response(text: str) -> dict[str, Any]:
    return {
        "errorCode": 0,
        "errorMsg": "Success",
        "result": {
            "dataInfo": {"type": "image", "width": 100.0, "height": 200.0},
            "ocrResults": [{"prunedResult": {"rec_texts": [text], "rec_boxes": [[0, 0, 10, 10]]}}],
        },
    }


def _ok(text: str) -> Any:
    def fake_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
        return _response(text)

    return fake_post


def _refuse_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
    raise AssertionError("post must not be called")


def _client(deployment: _Deployment) -> TestClient:
    return TestClient(build_app(deployment))


@pytest.fixture
def registry() -> ParserRegistry:
    """Two configured parsers, priority-tied, "paddle" winning alphabetically."""
    reg = ParserRegistry()
    reg.register(PaddleXParser(_endpoint("paddle"), post=_ok("INV-001")))
    reg.register(PaddleXParser(_endpoint("tesseract"), post=_ok("tess-line")))
    return reg


@pytest.fixture
def client(registry: ParserRegistry) -> TestClient:
    return _client(_Deployment(parsers=registry))


# -- POST /v1/ocr -------------------------------------------------------------


def test_ocr_returns_document_json(client: TestClient) -> None:
    response = client.post("/v1/ocr", params={"parser": "paddle"}, content=IMAGE)

    assert response.status_code == 200
    payload = response.json()
    assert "INV-001" in payload["text"]
    assert payload["provenance"]["parser_id"] == "paddle"


def test_ocr_without_parser_uses_capability_selection(client: TestClient) -> None:
    response = client.post("/v1/ocr", content=IMAGE)

    assert response.status_code == 200
    assert response.json()["provenance"]["parser_id"] == "paddle"


def test_ocr_unknown_parser_is_422(client: TestClient) -> None:
    response = client.post("/v1/ocr", params={"parser": "nope"}, content=IMAGE)

    assert response.status_code == 422
    message = response.json()["error"]["message"]
    assert "nope" in message
    assert "paddle" in message
    assert "tesseract" in message


def test_ocr_language_refused_is_422(client: TestClient) -> None:
    """Neither configured endpoint declares any language (Review Focus's own
    "no silent fallback"): asking for one is refused, not ignored."""
    response = client.post("/v1/ocr", params={"language": "vie"}, content=IMAGE)

    assert response.status_code == 422
    assert "language" in response.json()["error"]["message"]


def test_ocr_provider_failure_is_502(registry: ParserRegistry) -> None:
    def failing_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
        raise ProviderError(
            f"endpoint {endpoint.id!r} could not be reached",
            reason="transport",
            parser_id=endpoint.id,
        )

    registry.register(PaddleXParser(_endpoint("broken"), post=failing_post))
    response = _client(_Deployment(parsers=registry)).post(
        "/v1/ocr", params={"parser": "broken"}, content=IMAGE
    )

    assert response.status_code == 502


def test_ocr_stores_nothing(registry: ParserRegistry, tmp_path: Path) -> None:
    deployment = _Deployment(
        store=FileArtifactStore(tmp_path), blobs=BlobStore(tmp_path), parsers=registry
    )
    before = sorted(tmp_path.rglob("*"))

    response = _client(deployment).post("/v1/ocr", params={"parser": "paddle"}, content=IMAGE)

    assert response.status_code == 200
    assert sorted(tmp_path.rglob("*")) == before


# -- GET /v1/parsers -----------------------------------------------------------


def test_parsers_lists_configured_with_absent_not_null() -> None:
    reg = ParserRegistry()
    paddle = PaddleXParser(_endpoint("paddle"), post=_refuse_post)
    paddle.probe = lambda timeout_s=2.0: True  # type: ignore[method-assign]
    reg.register(paddle)

    tesseract = PaddleXParser(_endpoint("tesseract", languages=("eng", "vie")), post=_refuse_post)
    tesseract.probe = lambda timeout_s=2.0: False  # type: ignore[method-assign]
    reg.register(tesseract)

    reg.register_unavailable(
        "down",
        ParserCapabilities(text=True, media_types=frozenset({PDF})),
        reason="credentials_not_configured",
    )

    payload = _client(_Deployment(parsers=reg)).get("/v1/parsers").json()
    by_id = {entry["id"]: entry for entry in payload["parsers"]}

    paddle_entry = by_id["paddle"]
    assert paddle_entry["available"] is True
    assert "unavailable_reason" not in paddle_entry
    assert "languages" not in paddle_entry
    assert paddle_entry["healthy"] is True
    assert paddle_entry["capabilities"] == {
        "text": True,
        "geometry": True,
        "tables": False,
        "handwriting": False,
    }
    assert sorted(paddle_entry["media_types"]) == sorted({PDF, JPEG, PNG})

    tesseract_entry = by_id["tesseract"]
    assert tesseract_entry["languages"] == ["eng", "vie"]
    assert tesseract_entry["healthy"] is False

    down_entry = by_id["down"]
    assert down_entry["available"] is False
    assert down_entry["unavailable_reason"] == "credentials_not_configured"
    assert "languages" not in down_entry
    assert "healthy" not in down_entry


def test_parsers_reports_probe_result() -> None:
    reg = ParserRegistry()
    healthy = PaddleXParser(_endpoint("paddle"), post=_refuse_post)
    healthy.probe = lambda timeout_s=2.0: True  # type: ignore[method-assign]
    reg.register(healthy)

    unhealthy = PaddleXParser(_endpoint("tesseract"), post=_refuse_post)
    unhealthy.probe = lambda timeout_s=2.0: False  # type: ignore[method-assign]
    reg.register(unhealthy)

    payload = _client(_Deployment(parsers=reg)).get("/v1/parsers").json()
    by_id = {entry["id"]: entry for entry in payload["parsers"]}

    assert by_id["paddle"]["healthy"] is True
    assert by_id["tesseract"]["healthy"] is False


# -- Review Focus 3, the HTTP half --------------------------------------------


def test_no_secret_in_parsers_or_errors() -> None:
    secret_headers = {"Authorization": "Basic SEKRIT"}
    secret_endpoint = _endpoint(
        "secret", headers=secret_headers, url="http://user:pw@ocr.internal/ocr"
    )

    def failing_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
        raise ProviderError(
            f"endpoint {endpoint.id!r} rejected the credential",
            reason="auth",
            parser_id=endpoint.id,
        )

    reg = ParserRegistry()
    reg.register(PaddleXParser(secret_endpoint, post=failing_post))
    client = _client(_Deployment(parsers=reg))

    listing = client.get("/v1/parsers")
    ocr_error = client.post("/v1/ocr", params={"parser": "secret"}, content=IMAGE)

    assert ocr_error.status_code == 502
    for secret in ("SEKRIT", "pw", "ocr.internal"):
        assert secret not in listing.text
        assert secret not in ocr_error.text


# -- Authentication ------------------------------------------------------------


def test_routes_require_auth_when_enabled(registry: ParserRegistry) -> None:
    client = _client(_Deployment(parsers=registry, keys=KeyRing.from_file(Path(KEYS_FILE))))

    no_auth_list = client.get("/v1/parsers")
    no_auth_ocr = client.post("/v1/ocr", content=IMAGE)

    assert no_auth_list.status_code == 401
    assert no_auth_ocr.status_code == 401

    authenticated = client.get("/v1/parsers", headers=bearer("acme"))
    assert authenticated.status_code == 200
