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
import datetime as dt
import email.utils
import http.server
import io
import json
import socket
import subprocess
import sys
import threading
import time
import urllib.request
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
    timeout_s: float | None = None,
    deadline_s: float | None = None,
) -> OcrEndpoint:
    return OcrEndpoint(
        id=endpoint_id,
        url=url,
        version=version,
        headers=headers or {},
        languages=languages,
        max_pages=max_pages,
        timeout_s=timeout_s,
        deadline_s=deadline_s,
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
        # Not "service": a short result is the service answering in full and
        # still coming back truncated, which a retry cannot fix (012 review,
        # Finding 5) -- see `ProviderError.REASONS`'s own comment.
        assert error.reason == "incomplete"
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


class TestSharedDeadline:
    """`analyze_with_retries` opens its own `Deadline` when none is given --
    fine for every other caller, which makes one call -- but `parse()` here
    makes one call *per chunk*. Without a shared `Deadline`, a 20-chunk PDF
    gets twenty times `transport.deadline_s`, not one; this pins that it
    does not, using a fake clock so the test runs in real time regardless of
    the (fake) budget.
    """

    def test_the_budget_is_spent_once_not_per_chunk(self, monkeypatch: pytest.MonkeyPatch) -> None:
        data = _build_pdf(3)
        clock = {"t": 0.0}

        def fake_monotonic() -> float:
            return clock["t"]

        monkeypatch.setattr("docdoc.ingest.options.time.monotonic", fake_monotonic)

        calls = {"n": 0}

        def fake_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
            calls["n"] += 1
            clock["t"] += 10.0  # a slow chunk -- 10s "elapses" per call
            n = _page_count(base64.b64decode(body["file"]))
            return _pdf_response(n)

        source = SourceFile.from_bytes(data, filename="three.pdf")
        parser = PaddleXParser(_endpoint(max_pages=1), post=fake_post)
        # One shared budget of 15s. Chunk 1 passes the pre-flight check at
        # t=0 (15 remaining) and advances the clock to 10; chunk 2 passes at
        # t=10 (5 remaining) and advances it to 20; chunk 3's pre-flight
        # check at t=20 sees -5 remaining and must fail *before* a third
        # `fake_post` call -- a fresh 15s budget reopened per chunk would
        # never see less than 5 remaining and all three would succeed.
        transport = TransportSettings(max_attempts=1, deadline_s=15.0)

        with pytest.raises(ProviderError) as excinfo:
            parser.parse(source, {}, transport)

        assert excinfo.value.reason == "deadline"
        assert calls["n"] == 2


class TestEndpointOwnTimeouts:
    """Finding 4 of the 012 review: CPU OCR of a multi-page scan can run far
    longer than `TransportSettings`' generic 30s/120s defaults assume, and
    only the endpoint's own `timeout_s` / `deadline_s` -- not the caller's
    `TransportSettings` -- knows how slow *this* deployment's hardware is.
    """

    def test_endpoint_timeout_overrides_the_callers_transport(self) -> None:
        calls: list[float] = []

        def fake_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
            calls.append(timeout_s)
            return _image_response()

        endpoint = _endpoint(timeout_s=300.0, deadline_s=1800.0)
        parser = PaddleXParser(endpoint, post=fake_post)
        source = _source_image("sample_page.png")

        # The caller's own (much shorter) defaults -- the endpoint's values
        # must win.
        parser.parse(source, {}, TransportSettings())

        assert calls == [300.0]

    def test_an_unset_endpoint_timeout_leaves_the_callers_transport_alone(self) -> None:
        calls: list[float] = []

        def fake_post(endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float) -> Any:
            calls.append(timeout_s)
            return _image_response()

        parser = PaddleXParser(_endpoint(), post=fake_post)
        source = _source_image("sample_page.png")

        parser.parse(source, {}, TransportSettings(attempt_timeout_s=7.0, deadline_s=9.0))

        assert calls == [7.0]


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


@contextlib.contextmanager
def _stalling_server():
    """A server that accepts the connection, sends response headers, and then
    stalls mid-response -- long enough that a short client timeout always
    wins. `urlopen` only wraps the *connection* phase in `URLError`; this is
    what reproduces the bare `TimeoutError` `response.read()` raises once the
    connection itself succeeded (the gap `_post_over_the_wire` closes).
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", 0))
            self.rfile.read(length)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            time.sleep(2.0)
            with contextlib.suppress(Exception):
                self.wfile.write(b"{}")

        def log_message(self, log_format: str, *args: object) -> None:
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port
    finally:
        httpd.shutdown()
        httpd.server_close()


_STATUS_TABLE: tuple[tuple[int, dict[str, str], type[Exception], str, float | None], ...] = (
    (401, {}, ProviderError, "auth", None),
    (403, {}, ProviderError, "auth", None),
    (429, {"Retry-After": "3"}, ProviderError, "rate_limit", 3.0),
    (400, {}, UnsupportedDocumentError, "corrupt", None),
    (413, {}, UnsupportedDocumentError, "page_limit", None),
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

    def test_stalled_response_is_a_timeout_error(self) -> None:
        with _stalling_server() as port:
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/ocr")
            with pytest.raises(ProviderError) as excinfo:
                paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 0.3)
        assert excinfo.value.reason == "timeout"

    def test_headers_are_sent(self) -> None:
        with _server(200, body=b'{"errorCode":0,"result":{}}') as (port, received):
            endpoint = _endpoint(
                url=f"http://127.0.0.1:{port}/ocr", headers={"X-Api-Key": "abc123"}
            )
            paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)
        assert received.get("X-Api-Key") == "abc123"

    def test_non_utf8_body_is_a_service_error(self) -> None:
        # `json.loads` on non-UTF-8 bytes raises `UnicodeDecodeError`, not
        # `json.JSONDecodeError` -- both are `ValueError`, but only one is
        # caught by `except json.JSONDecodeError`. This body is never valid
        # UTF-8 (0xFF is not a continuation byte anywhere), so it must still
        # translate rather than escape as a raw `UnicodeDecodeError`.
        with _server(200, body=b"\xff\xfe\x00\xff") as (port, _received):
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/ocr")
            with pytest.raises(ProviderError) as excinfo:
                paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)
        assert excinfo.value.reason == "service"


class TestRetryAfterParsing:
    """FR-038's interval has two wire forms (RFC 9110): a delta in seconds,
    already covered by `_STATUS_TABLE`'s 429 row, and an HTTP-date. Neither
    form, nor garbage that is neither, may ever raise out of translation.
    """

    def test_http_date_form_is_parsed(self) -> None:
        future = email.utils.format_datetime(dt.datetime.now(dt.UTC) + dt.timedelta(seconds=30))
        with _server(429, body=b"{}", extra_headers={"Retry-After": future}) as (port, _received):
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/ocr")
            with pytest.raises(ProviderError) as excinfo:
                paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)

        assert excinfo.value.reason == "rate_limit"
        assert excinfo.value.retry_after_s is not None
        # Generous bounds: this runs for real, so some seconds pass between
        # the header being built above and the assertion running here.
        assert 20.0 <= excinfo.value.retry_after_s <= 31.0

    def test_unparseable_value_is_ignored_not_fatal(self) -> None:
        with _server(429, body=b"{}", extra_headers={"Retry-After": "not a date or a number"}) as (
            port,
            _received,
        ):
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/ocr")
            with pytest.raises(ProviderError) as excinfo:
                paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)

        assert excinfo.value.reason == "rate_limit"
        assert excinfo.value.retry_after_s is None
        # The real bug this guards: a garbage Retry-After must not crash the
        # translation and chain a new exception onto the URL-bearing
        # HTTPError via __context__.
        assert excinfo.value.__context__ is None


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

    def test_stalled_response(self) -> None:
        with _stalling_server() as port:
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/ocr", headers=SECRET_HEADERS)
            with pytest.raises(ProviderError) as excinfo:
                paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 0.3)
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
            assert secret not in str(error.__context__)
        # Not just "clean of these three substrings" -- genuinely nothing:
        # `raise ... from None` alone leaves `__context__` pointing at the
        # original HTTPError/URLError, which the fix (raising after the
        # `except` block has already finished) removes entirely.
        assert error.__context__ is None


@contextlib.contextmanager
def _redirect_target():
    """A second local server: the one a redirect might send the credential
    to. Records whether it ever received a request at all, and what
    `Authorization` it carried if so -- `test_redirect_never_carries_the_
    credential` asserts both are nothing.
    """
    received: list[dict[str, str]] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def _respond(self) -> None:
            received.append(dict(self.headers.items()))
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"{}")

        do_GET = _respond  # noqa: N815
        do_POST = _respond  # noqa: N815

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


@contextlib.contextmanager
def _redirecting_server(*, status: int, target_port: int):
    """A server that answers every request with a redirect to
    `_redirect_target`'s port -- the first hop a credential must never
    survive past.
    """

    class Handler(http.server.BaseHTTPRequestHandler):
        def _respond(self) -> None:
            self.send_response(status)
            self.send_header("Location", f"http://127.0.0.1:{target_port}/ocr")
            self.send_header("Content-Length", "0")
            self.end_headers()

        do_GET = _respond  # noqa: N815
        do_POST = _respond  # noqa: N815

        def log_message(self, log_format: str, *args: object) -> None:
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port
    finally:
        httpd.shutdown()
        httpd.server_close()


class TestRedirectsAreRefused:
    """Finding 1 of the 012 review: CPython's default `urlopen` follows a
    301/302/303 and copies every header -- including `Authorization` -- onto
    the redirected request. These two local servers pin that docdoc's own
    opener never gets that far: the second server must receive nothing at
    all, not merely a request stripped of the header.
    """

    @pytest.mark.parametrize("status", [301, 302, 303, 307])
    def test_redirect_never_carries_the_credential(self, status: int) -> None:
        with (
            _redirect_target() as (target_port, received),
            _redirecting_server(status=status, target_port=target_port) as redirect_port,
        ):
            endpoint = _endpoint(
                url=f"http://127.0.0.1:{redirect_port}/ocr", headers=SECRET_HEADERS
            )
            with pytest.raises(ProviderError) as excinfo:
                paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)

        assert received == [], "the redirect target must receive no request at all"
        assert excinfo.value.reason == "transport"
        for secret in SECRETS:
            assert secret not in str(excinfo.value)

    def test_probe_false_on_redirect(self) -> None:
        with (
            _redirect_target() as (target_port, received),
            _redirecting_server(status=302, target_port=target_port) as redirect_port,
        ):
            endpoint = _endpoint(
                url=f"http://127.0.0.1:{redirect_port}/ocr", headers=SECRET_HEADERS
            )
            parser = PaddleXParser(endpoint)

            assert parser.probe(timeout_s=2.0) is False

        assert received == []

    def test_a_host_opener_installed_later_does_not_undo_the_refusal(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """docdoc is a library, and the application around it owns the
        process-wide opener. One installed after docdoc was imported -- a
        proxy, a cookie jar, or just the default that follows redirects --
        must not quietly hand the credential back to a redirect target.
        """
        monkeypatch.setattr(urllib.request, "_opener", None)
        urllib.request.install_opener(urllib.request.build_opener())

        with (
            _redirect_target() as (target_port, received),
            _redirecting_server(status=302, target_port=target_port) as redirect_port,
        ):
            endpoint = _endpoint(
                url=f"http://127.0.0.1:{redirect_port}/ocr", headers=SECRET_HEADERS
            )
            with pytest.raises(ProviderError):
                paddlex._post_over_the_wire(endpoint, {"file": "", "fileType": 0}, 2.0)
            assert PaddleXParser(endpoint).probe(timeout_s=2.0) is False

        assert received == []


def test_importing_the_adapter_leaves_the_process_opener_alone() -> None:
    """The redirect refusal belongs to this adapter's own requests, not to
    every `urlopen` in the host application. Asked in a fresh interpreter,
    because this suite's process has long since imported the module.
    """
    probe = (
        "import urllib.request\n"
        "import docdoc.ingest.parsers.paddlex\n"
        "print(urllib.request._opener is None)\n"
    )
    completed = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )
    assert completed.stdout.strip() == "True"


@contextlib.contextmanager
def _health_server(*, error_code: int = 0):
    """A server that answers GET with a health-shaped body on whatever path
    it's asked for, recording every path requested -- what `test_probe_*`
    needs to confirm `probe()` asks for `.../health`, not `.../ocr/health`.
    """
    paths: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            paths.append(self.path)
            body = json.dumps({"errorCode": error_code, "errorMsg": "Healthy"}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, log_format: str, *args: object) -> None:
            pass

    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd.server_port, paths
    finally:
        httpd.shutdown()
        httpd.server_close()


class TestProbe:
    def test_probe_false_when_unreachable(self) -> None:
        endpoint = _endpoint(url=f"http://127.0.0.1:{_closed_port()}/ocr")
        parser = PaddleXParser(endpoint)

        assert parser.probe(timeout_s=0.5) is False

    def test_probe_true_when_healthy(self) -> None:
        # Today only the unreachable case was tested, so a probe that always
        # returned False would have passed -- this is the case that proves
        # it actually asks the right URL and reads the right field.
        with _health_server() as (port, paths):
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/ocr")
            parser = PaddleXParser(endpoint)

            assert parser.probe(timeout_s=1.0) is True
        assert paths == ["/health"]

    def test_probe_joins_an_nginx_prefixed_path(self) -> None:
        # `endpoint.url` is the full POST URL; the health route sits beside
        # it, not under it: `.../tesseract/ocr` -> `.../tesseract/health`,
        # not `.../tesseract/ocr/health`.
        with _health_server() as (port, paths):
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/tesseract/ocr")
            parser = PaddleXParser(endpoint)

            assert parser.probe(timeout_s=1.0) is True
        assert paths == ["/tesseract/health"]

    def test_probe_false_when_errorcode_nonzero(self) -> None:
        with _health_server(error_code=1) as (port, _paths):
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/ocr")
            parser = PaddleXParser(endpoint)

            assert parser.probe(timeout_s=1.0) is False

    def test_probe_false_when_body_is_not_an_object(self) -> None:
        # A JSON list/string body makes `.get("errorCode")` raise
        # `AttributeError` unless probe() guards for it -- "never raises"
        # must hold for a malformed health body too, not just an unreachable
        # endpoint.
        with _server(200, body=b"[1, 2, 3]") as (port, _received):
            endpoint = _endpoint(url=f"http://127.0.0.1:{port}/ocr")
            parser = PaddleXParser(endpoint)

            assert parser.probe(timeout_s=1.0) is False
