"""`POST /v1/ocr` and `GET /v1/parsers` -- the raw OCR gateway over HTTP.

Every registry here is built from fake-post `PaddleXParser`s, and every
`.probe` that could otherwise reach a fictitious host (`paddle.example`,
`ocr.internal`) is replaced before it is ever registered. `_no_sockets` below
is the backstop for both: an autouse fixture that makes `socket.socket.connect`
raise, so a `.probe` or a `post` this file forgot to stub fails loudly, in this
process, rather than quietly reaching a real host -- which for the endpoint in
`test_no_secret_in_parsers_or_errors` would mean sending `Basic SEKRIT`
somewhere outside this test run.

`test_no_secret_in_parsers_or_errors` -- Review Focus 3's HTTP half -- still
exercises the *real* translation path for the one case that matters most: a
monkeypatched `paddlex._OPENER.open` raises the same `HTTPError` a real
service would, so the 502 body is produced by `PaddleXParser`'s actual
`_post_over_the_wire`/`_from_http_error`, not by a fake `post=` that only
proves the HTTP route itself adds nothing.
"""

from __future__ import annotations

import socket
import urllib.error
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
from docdoc.ingest.parsers import paddlex
from docdoc.ingest.parsers.paddlex import OcrEndpoint, PaddleXParser
from docdoc.ingest.registry import ParserRegistry
from docdoc.ingest.source import JPEG, PDF, PNG

IMAGE = (Path(__file__).parent.parent / "fixtures" / "image" / "sample_page.png").read_bytes()


@pytest.fixture(autouse=True)
def _no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Nothing in this file may open a real connection.

    Every test here is expected to stub `.probe` and to route `post` through a
    fake or through a monkeypatched `paddlex._OPENER.open` -- never through a real socket.
    This is the backstop for the one that matters: a `PaddleXParser` built from
    a secret-bearing `OcrEndpoint` whose `.probe` or `post` was left real would
    otherwise send `Basic SEKRIT` to whatever actually answers at the host the
    test made up.
    """

    real_connect = socket.socket.connect

    def _refuse_connect(self: socket.socket, address: Any, *args: Any, **kwargs: Any) -> Any:
        # Loopback stays open: on Windows the event loop TestClient starts
        # builds its self-pipe with the emulated `socket.socketpair`, which
        # connects to 127.0.0.1. Refusing that broke every test here on
        # Windows while proving nothing -- no endpoint in this file is local.
        host = address[0] if isinstance(address, tuple) else address
        if host in _LOOPBACK:
            return real_connect(self, address, *args, **kwargs)
        raise OSError("tests/contract/test_ocr_routes.py opens no real socket")

    monkeypatch.setattr(socket.socket, "connect", _refuse_connect)


_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


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


def _stub_probe(parser: PaddleXParser, *, healthy: bool = True) -> PaddleXParser:
    """Replace a real `.probe` with a fixed answer -- no real `urlopen` call.

    Every parser this file registers goes through this (or is never probed at
    all), which is what keeps `GET /v1/parsers` deterministic here instead of
    depending on `_no_sockets` to turn a would-be real probe into `False`.
    """
    parser.probe = lambda timeout_s=2.0: healthy  # type: ignore[method-assign]
    return parser


@pytest.fixture
def registry() -> ParserRegistry:
    """Two configured parsers, priority-tied, "paddle" winning alphabetically."""
    reg = ParserRegistry()
    reg.register(_stub_probe(PaddleXParser(_endpoint("paddle"), post=_ok("INV-001"))))
    reg.register(_stub_probe(PaddleXParser(_endpoint("tesseract"), post=_ok("tess-line"))))
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
    paddle = _stub_probe(PaddleXParser(_endpoint("paddle"), post=_refuse_post), healthy=True)
    reg.register(paddle)

    tesseract = _stub_probe(
        PaddleXParser(_endpoint("tesseract", languages=("eng", "vie")), post=_refuse_post),
        healthy=False,
    )
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
    reg.register(_stub_probe(PaddleXParser(_endpoint("paddle"), post=_refuse_post), healthy=True))
    reg.register(
        _stub_probe(PaddleXParser(_endpoint("tesseract"), post=_refuse_post), healthy=False)
    )

    payload = _client(_Deployment(parsers=reg)).get("/v1/parsers").json()
    by_id = {entry["id"]: entry for entry in payload["parsers"]}

    assert by_id["paddle"]["healthy"] is True
    assert by_id["tesseract"]["healthy"] is False


# -- Review Focus 3, the HTTP half --------------------------------------------


#: The endpoint's own secrets, checked for absence against every surface below.
SECRET_URL = "http://user:pw@ocr.internal/ocr"
SECRETS = ("SEKRIT", "pw@", "ocr.internal")


def test_no_secret_in_parsers_or_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    secret = _stub_probe(
        PaddleXParser(
            _endpoint("secret", headers={"Authorization": "Basic SEKRIT"}, url=SECRET_URL)
        ),
        healthy=False,
    )
    reg = ParserRegistry()
    reg.register(secret)
    client = _client(_Deployment(parsers=reg))

    # Never a URL or a header in the listing itself -- true by construction
    # (`ParserChoice` has no field that could carry either), checked anyway.
    listing = client.get("/v1/parsers")
    for leak in SECRETS:
        assert leak not in listing.text

    # Nor in a refusal that names every *configured* id -- "secret" itself is
    # not a secret (ids are the one thing every error is allowed to name an
    # endpoint by), but the url/header behind it must still not surface.
    unknown = client.post("/v1/ocr", params={"parser": "nope"}, content=IMAGE)
    assert unknown.status_code == 422
    for leak in SECRETS:
        assert leak not in unknown.text

    # Nor when the real transport actually fails. The adapter's own opener is
    # monkeypatched rather than `post=`, so this exercises `PaddleXParser`'s
    # own `_post_over_the_wire` / `_from_http_error` translation -- the real
    # `HTTPError` carries `SECRET_URL` on itself, and it still must not reach
    # the response, because `_from_http_error` reads only `.code` and
    # `.headers`, never `.url` or `.filename`.
    def fake_open(request: Any, timeout: float | None = None) -> Any:
        raise urllib.error.HTTPError(SECRET_URL, 500, "boom", None, None)  # type: ignore[arg-type]

    monkeypatch.setattr(paddlex._OPENER, "open", fake_open)
    failure = client.post("/v1/ocr", params={"parser": "secret"}, content=IMAGE)
    assert failure.status_code == 502
    for leak in SECRETS:
        assert leak not in failure.text


# -- Authentication ------------------------------------------------------------


def test_routes_require_auth_when_enabled(registry: ParserRegistry) -> None:
    client = _client(_Deployment(parsers=registry, keys=KeyRing.from_file(Path(KEYS_FILE))))

    no_auth_list = client.get("/v1/parsers")
    no_auth_ocr = client.post("/v1/ocr", content=IMAGE)

    assert no_auth_list.status_code == 401
    assert no_auth_ocr.status_code == 401

    # The refusal is the router dependency, not a route that happens to be
    # broken -- a valid credential reaches both exactly as any other `/v1`
    # route would.
    authenticated_list = client.get("/v1/parsers", headers=bearer("acme"))
    assert authenticated_list.status_code == 200

    authenticated_ocr = client.post(
        "/v1/ocr", params={"parser": "paddle"}, content=IMAGE, headers=bearer("acme")
    )
    assert authenticated_ocr.status_code == 200
