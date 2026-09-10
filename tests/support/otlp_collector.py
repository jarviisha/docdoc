"""A real OTLP/HTTP collector, because a bridge that exports nothing raises nothing.

The webhook tests learned this first: `tests/support/webhook_receiver.py` exists
because "mocking the transport would test nothing about the destination policy".
The same argument applies here and was not applied in time.

**What it cost.** `bridge()` built a `TracerProvider`, attached an OTLP exporter
to it, and then took its tracer from `trace.get_tracer(...)` — which reads the
*global* provider, not the one just built. Every span went to the default no-op
tracer. docdoc exported **zero spans**, from the day telemetry landed until a
collector was pointed at it and observed to receive nothing.

Four `otel`-marked tests passed throughout, because every one of them asserted
that emitting does not raise — and emitting into a no-op tracer does not raise.
The only assertion that could have caught it is the one this module makes
possible: point the bridge at a collector you can read, and check that a span
arrived.

Stdlib only, on an ephemeral port, exactly as the webhook receiver is.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

__all__ = ["OtlpCollector"]


@dataclass
class OtlpCollector:
    """Accepts `POST /v1/traces` and records what arrived.

    It does **not** decode protobuf. What is under test is whether docdoc's
    exporter delivers anything at all, and a test that parsed the payload would
    be testing `opentelemetry-proto` — which is not ours and is not what broke.
    """

    #: One entry per delivered batch: the raw body. Length is the assertion.
    batches: list[bytes] = field(default_factory=list)
    _server: Any = None
    _thread: Any = None

    def __enter__(self) -> OtlpCollector:
        collector = self

        class _Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # the standard library's spelling
                length = int(self.headers.get("Content-Length") or 0)
                collector.batches.append(self.rfile.read(length))
                self.send_response(200)
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
    def endpoint(self) -> str:
        """Where to point `DOCDOC_OTLP_ENDPOINT`, on a port nothing else holds."""
        port = self._server.server_address[1]
        return f"http://127.0.0.1:{port}/v1/traces"

    @property
    def received(self) -> int:
        return len(self.batches)
