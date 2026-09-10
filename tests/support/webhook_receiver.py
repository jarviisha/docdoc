"""A real HTTP receiver, because mocking the transport would test nothing.

T116. The thing under test in `docdoc.runs.delivery` is *the transport*: which
address a connection goes to, which hostname it presents, whether a redirect is
followed, and whether the bytes a receiver verifies are the bytes that were
signed. A fake that intercepts `_post` answers none of those, and would pass
against an implementation that connected to whatever `getaddrinfo` returned at
connect time — which is the bug the destination policy exists to prevent.

So this is `http.server`, on a real socket, on a real port. It records what it
was sent and verifies signatures with the same rule a customer's receiver would
have to implement from `docs/concepts/delivery.md` — written from that document
rather than by calling `delivery.signature`, so the two agreeing means the
documentation is right rather than that one function equals itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

__all__ = ["Received", "WebhookReceiver"]


@dataclass(frozen=True)
class Received:
    """One delivery, as the receiver saw it."""

    body: bytes
    signature: str | None

    @property
    def payload(self) -> dict[str, Any]:
        return dict(json.loads(self.body))

    def verifies(self, secret: str) -> bool:
        """The verification a customer implements from the documentation.

        Deliberately written out rather than delegating to
        `delivery.signature`: this is the check the other side of the contract
        performs, and implementing it here is what makes "a receiver can verify
        it" a tested claim rather than a restatement.
        """
        if not self.signature:
            return False
        parts = dict(piece.split("=", 1) for piece in self.signature.split(",") if "=" in piece)
        timestamp, provided = parts.get("t"), parts.get("v1")
        if not timestamp or not provided:
            return False
        signed = f"{timestamp}.".encode() + self.body
        expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, provided)

    @property
    def timestamp(self) -> int | None:
        """The `t` the signature covers, which is what makes replay detectable."""
        if not self.signature:
            return None
        for piece in self.signature.split(","):
            name, _, value = piece.partition("=")
            if name == "t":
                return int(value)
        return None


@dataclass
class WebhookReceiver:
    """A receiver that answers what it is told to and records what it got.

    ``status`` is mutable so one test can watch a receiver fail, be fixed, and
    take the delivery on a later attempt — which is the behaviour retries exist
    for and cannot be observed against a receiver that only ever does one thing.
    """

    status: int = 200
    #: Where a `3xx` points. Set alongside `status` to test that a redirect is
    #: **not** followed (FR-061).
    location: str | None = None
    received: list[Received] = field(default_factory=list)
    _server: Any = None
    _thread: Any = None

    def __enter__(self) -> WebhookReceiver:
        receiver = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # the standard library's spelling
                length = int(self.headers.get("Content-Length") or 0)
                receiver.received.append(
                    Received(
                        body=self.rfile.read(length),
                        signature=self.headers.get("X-Docdoc-Signature"),
                    )
                )
                self.send_response(receiver.status)
                if receiver.location:
                    self.send_header("Location", receiver.location)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
                """Silent. A test's output is its assertions."""

        self._server = HTTPServer(("127.0.0.1", 0), _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *_: object) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)

    @property
    def url(self) -> str:
        """Where to register. `http` and loopback, so a test of this receiver is
        necessarily also a test that `allow_private` is what permits both."""
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/hook"
