"""`PaddleXParser` -- the HTTP half of the PaddleX-protocol adapter: sending a
request, splitting a long PDF into endpoint-sized chunks, stitching the
results, and mapping every failure into docdoc's error model with no secret
and no network call for anything the adapter can refuse up front.

Every test here replaces `post` with a fake and never opens a socket, except
the HTTP-mapping and secrets tests, which run `_post_over_the_wire` -- the
default implementation -- against a real `http.server.ThreadingHTTPServer` on
localhost, so the status-code table is pinned against urllib's actual
behaviour rather than against this module's assumptions about it.
"""

from __future__ import annotations

import base64
import contextlib
import http.server
import io
import socket
import threading
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pypdfium2")

import pypdfium2 as pdfium

from docdoc.ingest.errors import ParserCapabilityError, ProviderError, UnsupportedDocumentError
from docdoc.ingest.options import TransportSettings
from docdoc.ingest.parsers import paddlex
from docdoc.ingest.parsers.paddlex import OcrEndpoint, PaddleXParser
from docdoc.ingest.source import SourceFile

FIXTURES = Path(__file__).parent.parent / "fixtures"
PDF_FIXTURES = FIXTURES / "pdf"
IMAGE_FIXTURES = FIXTURES / "image"

#: One attempt, a short deadline: nothing in this file wants a real retry
#: loop, so transient failures must fail fast rather than sleep.
FAST_TRANSPORT = TransportSettings(max_attempts=1, deadline_s=5.0)

SECRET_HEADERS = {"Authorization": "Basic SEKRIT"}
SECRETS = ("SEKRIT", "pw@", "127.0.0.1")


def _endpoint(
    *,
    endpoint_id: str = "paddle",
    url: str = "http://paddle.example/ocr",
    version: str = "1.0.0",
    headers: dict[str, str] | None = None,
    languages: tuple[str, ...] = (),
    max_pages: int | None = None,
) -> OcrEndpoint:
    return OcrEndpoint(
        id=endpoint_id,
        url=url,
        version=version,
        headers=headers or {},
        languages=languages,
        max_pages=max_pages,
    )


def _source_pdf(name: str) -> SourceFile:
    path = PDF_FIXTURES / name
    return SourceFile.from_bytes(path.read_bytes(), filename=name)


def _source_image(name: str) -> SourceFile:
    path = IMAGE_FIXTURES / name
    return SourceFile.from_bytes(path.read_bytes(), filename=name)


def _build_pdf(num_pages: int) -> bytes:
    """A PDF of exactly ``num_pages`` pages, built from the one-page
    ``digital_invoice.pdf`` fixture repeated -- content does not matter for a
    splitting test, only the page count and the order pages come back in.
    """
    source = pdfium.PdfDocument(PDF_FIXTURES / "digital_invoice.pdf")
    try:
        document = pdfium.PdfDocument.new()
        try:
            for _ in range(num_pages):
                document.import_pages(source, pages=[0])
            buffer = io.BytesIO()
            document.save(buffer)
            return buffer.getvalue()
        finally:
            document.close()
    finally:
        source.close()


def _page_count(chunk_bytes: bytes) -> int:
    document = pdfium.PdfDocument(chunk_bytes)
    try:
        return len(document)
    finally:
        document.close()


def _pdf_response(n_pages: int, *, texts: list[str] | None = None) -> dict[str, Any]:
    """A minimal, valid PaddleX-protocol ``/ocr`` response for ``n_pages``."""
    if texts is None:
        texts = [f"line{i}" for i in range(n_pages)]
    return {
        "errorCode": 0,
        "errorMsg": "Success",
        "result": {
            "dataInfo": {
                "type": "pdf",
                "pages": [{"width": 100.0, "height": 200.0} for _ in range(n_pages)],
            },
            "ocrResults": [
                {"prunedResult": {"rec_texts": [texts[i]], "rec_boxes": [[0, 0, 10, 10]]}}
                for i in range(n_pages)
            ],
        },
    }


def _image_response(text: str = "hello") -> dict[str, Any]:
    return {
        "errorCode": 0,
        "errorMsg": "Success",
        "result": {
            "dataInfo": {"type": "image", "width": 100.0, "height": 200.0},
            "ocrResults": [{"prunedResult": {"rec_texts": [text], "rec_boxes": [[0, 0, 10, 10]]}}],
        },
    }


def _refuse_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
    raise AssertionError("post must not be called")


# ---------------------------------------------------------------------------
# Sending: images once, PDFs whole or split.
# ---------------------------------------------------------------------------


class TestSending:
    def test_image_is_sent_once_as_filetype_1(self) -> None:
        calls: list[dict[str, Any]] = []

        def fake_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
            calls.append(body)
            return _image_response()

        source = _source_image("sample_page.png")
        parser = PaddleXParser(_endpoint(), post=fake_post)
        document = parser.parse(source, {}, FAST_TRANSPORT)

        assert len(calls) == 1
        assert calls[0]["fileType"] == 1
        assert base64.b64decode(calls[0]["file"]) == source.data
        assert document.text == "hello\n"

    def test_pdf_within_limit_is_sent_whole_as_filetype_0(self) -> None:
        calls: list[dict[str, Any]] = []

        def fake_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
            calls.append(body)
            return _pdf_response(1)

        source = _source_pdf("digital_invoice.pdf")
        parser = PaddleXParser(_endpoint(max_pages=5), post=fake_post)
        parser.parse(source, {}, FAST_TRANSPORT)

        assert len(calls) == 1
        assert calls[0]["fileType"] == 0
        assert base64.b64decode(calls[0]["file"]) == source.data

    def test_pdf_over_limit_is_split_and_stitched(self) -> None:
        data = _build_pdf(5)
        calls: list[dict[str, Any]] = []
        sent_so_far = 0

        def fake_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
            nonlocal sent_so_far
            calls.append(body)
            chunk = base64.b64decode(body["file"])
            n = _page_count(chunk)
            texts = [f"p{sent_so_far + i}" for i in range(n)]
            sent_so_far += n
            return _pdf_response(n, texts=texts)

        source = SourceFile.from_bytes(data, filename="five.pdf")
        parser = PaddleXParser(_endpoint(max_pages=2), post=fake_post)
        document = parser.parse(source, {}, FAST_TRANSPORT)

        assert [body["fileType"] for body in calls] == [0, 0, 0]
        sent_counts = [_page_count(base64.b64decode(body["file"])) for body in calls]
        assert sent_counts == [2, 2, 1]

        assert len(document.pages) == 5
        for index in range(5):
            (span,) = document.find(f"p{index}")
            (geometry,) = document.locate(span)
            assert geometry.page_index == index


# ---------------------------------------------------------------------------
# The silent-truncation defence (ING-7, record.py).
# ---------------------------------------------------------------------------


class TestShortResult:
    def test_short_chunk_is_provider_error(self) -> None:
        data = _build_pdf(2)

        def fake_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
            return _pdf_response(1)

        source = SourceFile.from_bytes(data, filename="two.pdf")
        parser = PaddleXParser(_endpoint(endpoint_id="paddle", max_pages=2), post=fake_post)

        with pytest.raises(ProviderError) as excinfo:
            parser.parse(source, {}, FAST_TRANSPORT)

        error = excinfo.value
        assert error.reason == "service"
        message = str(error)
        assert "paddle" in message
        assert "1" in message
        assert "2" in message

    def test_one_failing_chunk_fails_whole_parse(self) -> None:
        data = _build_pdf(6)
        attempts = {"n": 0}

        def fake_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
            attempts["n"] += 1
            if attempts["n"] == 2:
                raise ProviderError(
                    "endpoint rejected the credential", reason="auth", parser_id=endpoint.id
                )
            n = _page_count(base64.b64decode(body["file"]))
            return _pdf_response(n)

        source = SourceFile.from_bytes(data, filename="six.pdf")
        parser = PaddleXParser(_endpoint(max_pages=2), post=fake_post)

        with pytest.raises(ProviderError) as excinfo:
            parser.parse(source, {}, FAST_TRANSPORT)

        assert excinfo.value.reason == "auth"
        # The third chunk is never sent once the second has failed.
        assert attempts["n"] == 2


# ---------------------------------------------------------------------------
# Refused before any call: options, encryption, corruption, the missing extra.
# ---------------------------------------------------------------------------


class TestRefusedBeforeAnyCall:
    def test_language_not_declared_is_refused_before_any_call(self) -> None:
        parser = PaddleXParser(_endpoint(languages=("vie",)), post=_refuse_post)
        source = _source_image("sample_page.png")

        with pytest.raises(ParserCapabilityError):
            parser.parse(source, {"language": "fra"}, FAST_TRANSPORT)

    def test_unknown_option_is_refused(self) -> None:
        parser = PaddleXParser(_endpoint(), post=_refuse_post)
        source = _source_image("sample_page.png")

        with pytest.raises(ParserCapabilityError):
            parser.parse(source, {"dpi": 300}, FAST_TRANSPORT)

    def test_encrypted_pdf_refused_before_any_call(self) -> None:
        parser = PaddleXParser(_endpoint(), post=_refuse_post)
        source = _source_pdf("encrypted.pdf")

        with pytest.raises(UnsupportedDocumentError) as excinfo:
            parser.parse(source, {}, FAST_TRANSPORT)
        assert excinfo.value.reason == "encrypted"

    def test_corrupt_pdf_refused_before_any_call(self) -> None:
        parser = PaddleXParser(_endpoint(), post=_refuse_post)
        source = SourceFile.from_bytes(b"%PDF-" + b"not a real pdf" * 5, filename="junk.pdf")

        with pytest.raises(UnsupportedDocumentError) as excinfo:
            parser.parse(source, {}, FAST_TRANSPORT)
        assert excinfo.value.reason == "corrupt"

    def test_pdf_without_pypdfium2_extra_is_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(paddlex, "_pdfium_installed", lambda: False)
        parser = PaddleXParser(_endpoint(), post=_refuse_post)
        source = _source_pdf("digital_invoice.pdf")

        with pytest.raises(ParserCapabilityError) as excinfo:
            parser.parse(source, {}, FAST_TRANSPORT)
        assert excinfo.value.candidates == ((parser.id, False, "extra_not_installed"),)


# ---------------------------------------------------------------------------
# The HTTP transport: status mapping, headers, secrets, probing.
# ---------------------------------------------------------------------------


@contextlib.contextmanager
def _server(status: int, *, body: bytes = b"{}", extra_headers: dict[str, str] | None = None):
    """A `ThreadingHTTPServer` on an ephemeral localhost port, answering every
    request with a fixed status/body/headers and recording the request
    headers it received, for `test_headers_are_sent`.
    """
    extra_headers = extra_headers or {}
    received: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def _respond(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            received.update(dict(self.headers.items()))
            self.send_response(status)
            for key, value in extra_headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_POST = _respond  # noqa: N815
        do_GET = _respond  # noqa: N815

        def log_message(self, log_format: str, *args: object) -> None:
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port, received
    finally:
        httpd.shutdown()
        httpd.server_close()


def _closed_port() -> int:
    """A localhost port nothing is listening on, for a refused connection."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


_STATUS_TABLE: tuple[tuple[int, dict[str, str], type[Exception], str, float | None], ...] = (
    (401, {}, ProviderError, "auth", None),
    (403, {}, ProviderError, "auth", None),
    (429, {"Retry-After": "3"}, ProviderError, "rate_limit", 3.0),
    (400, {}, UnsupportedDocumentError, "corrupt", None),
    (415, {}, UnsupportedDocumentError, "corrupt", None),
    (422, {}, UnsupportedDocumentError, "corrupt", None),
    (500, {}, ProviderError, "service", None),
    (502, {}, ProviderError, "service", None),
    (503, {}, ProviderError, "service", None),
    (504, {}, ProviderError, "timeout", None),
)


class TestHttpStatusMapping:
    @pytest.mark.parametrize(
        ("status", "extra_headers", "expected_type", "expected_reason", "expected_retry_after"),
        _STATUS_TABLE,
    )
    def test_http_status_mapping(
        self,
        status: int,
        extra_headers: dict[str, str],
        expected_type: type[Exception],
        expected_reason: str,
        expected_retry_after: float | None,
    ) -> None:
        with _server(status, body=b'{"errorMsg":"boom"}', extra_headers=extra_headers) as (
            port,
            _received,
        ):
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/ocr")
            with pytest.raises(expected_type) as excinfo:
                paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)

        assert excinfo.value.reason == expected_reason
        if expected_retry_after is not None:
            assert excinfo.value.retry_after_s == expected_retry_after

    def test_non_json_body_is_a_service_error(self) -> None:
        with _server(200, body=b"not json") as (port, _received):
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/ocr")
            with pytest.raises(ProviderError) as excinfo:
                paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)
        assert excinfo.value.reason == "service"

    def test_refused_connection_is_a_transport_error(self) -> None:
        endpoint = _endpoint(url=f"http://127.0.0.1:{_closed_port()}/ocr")
        with pytest.raises(ProviderError) as excinfo:
            paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)
        assert excinfo.value.reason == "transport"

    def test_headers_are_sent(self) -> None:
        with _server(200, body=b'{"errorCode":0,"result":{}}') as (port, received):
            endpoint = _endpoint(
                url=f"http://127.0.0.1:{port}/ocr", headers={"X-Api-Key": "abc123"}
            )
            paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)
        assert received.get("X-Api-Key") == "abc123"


class TestNoSecretInAnyFailure:
    """Review Focus 3, pinned here and in Task 5: an endpoint whose header
    carries `Authorization: Basic SEKRIT` fails every way the table above
    does, and none of them may let the secret -- or the host, however it was
    configured -- survive into the error.
    """

    @pytest.mark.parametrize(
        ("status", "extra_headers", "expected_type"),
        [(row[0], row[1], row[2]) for row in _STATUS_TABLE],
    )
    def test_http_status(
        self, status: int, extra_headers: dict[str, str], expected_type: type[Exception]
    ) -> None:
        with _server(status, body=b'{"errorMsg":"boom"}', extra_headers=extra_headers) as (
            port,
            _received,
        ):
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/ocr", headers=SECRET_HEADERS)
            with pytest.raises(expected_type) as excinfo:
                paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)
        self._assert_clean(excinfo.value)

    def test_non_json_body(self) -> None:
        with _server(200, body=b"not json") as (port, _received):
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/ocr", headers=SECRET_HEADERS)
            with pytest.raises(ProviderError) as excinfo:
                paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)
        self._assert_clean(excinfo.value)

    def test_refused_connection(self) -> None:
        endpoint = _endpoint(url=f"http://127.0.0.1:{_closed_port()}/ocr", headers=SECRET_HEADERS)
        with pytest.raises(ProviderError) as excinfo:
            paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)
        self._assert_clean(excinfo.value)

    def test_credential_laden_url_that_cannot_even_connect(self) -> None:
        # The URL itself may carry credentials -- a misconfiguration docdoc
        # never relies on -- and even a connection failing because of it must
        # not let "pw@" or the host leak into the error.
        endpoint = _endpoint(
            url=f"http://user:pw@127.0.0.1:{_closed_port()}/ocr", headers=SECRET_HEADERS
        )
        with pytest.raises(ProviderError) as excinfo:
            paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)
        self._assert_clean(excinfo.value)

    @staticmethod
    def _assert_clean(error: Exception) -> None:
        for secret in SECRETS:
            assert secret not in str(error)
            assert secret not in str(error.__cause__)


class TestProbe:
    def test_probe_false_when_unreachable(self) -> None:
        endpoint = _endpoint(url=f"http://127.0.0.1:{_closed_port()}/ocr")
        parser = PaddleXParser(endpoint)

        assert parser.probe(timeout_s=0.5) is False
