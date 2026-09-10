#!/usr/bin/env python3
"""A webhook receiver that verifies what docdoc signed.

Run it, register the URL it prints, and finish a run:

    python examples/receive_webhook.py                 # listens on :8787

    curl -X POST http://localhost:8000/v1/callbacks \\
      -H 'content-type: application/json' \\
      -d '{"url": "https://your-host/hook", "secret": "whsec_..."}'

**Standard library only, and deliberately.** Verifying a docdoc delivery needs
`hmac`, `hashlib`, and the raw request body. If this example needed a framework,
the signing scheme would be harder than it should be.

The two things that are easy to get wrong are both here with the reason attached:
verify against the **raw bytes**, and check the **timestamp**.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

#: The same value the deployment has in its `DOCDOC_DELIVERY_SECRETS_FILE` and
#: that was passed when the callback was registered.
SECRET = os.environ.get("WEBHOOK_SECRET", "whsec_change_me")

#: How old a delivery may be before this receiver refuses it. Five minutes is a
#: common choice; the number is yours, and the point is that there **is** one.
#: Without a freshness window the timestamp in the signature buys nothing.
TOLERANCE_SECONDS = 300

PORT = int(os.environ.get("PORT", "8787"))


def verify(body: bytes, header: str | None, *, secret: str, now: float) -> tuple[bool, str]:
    """`(ok, reason)` for one delivery.

    Two checks, and both are necessary:

    **The signature covers `f"{t}.{body}"`.** Verify against the bytes that
    arrived — re-serialising the JSON first will change them (key order,
    whitespace, unicode escaping) and the signature will not match. That is the
    single most common integration failure with any scheme of this shape.

    **The timestamp is inside the signed material**, so a captured delivery
    cannot be replayed as fresh: moving `t` invalidates the signature, and
    keeping `t` fails the window below.
    """
    if not header:
        return False, "no signature header"

    parts = dict(piece.split("=", 1) for piece in header.split(",") if "=" in piece)
    timestamp, provided = parts.get("t"), parts.get("v1")
    if not timestamp or not provided:
        return False, "malformed signature header"

    signed = f"{timestamp}.".encode() + body
    expected = hmac.new(secret.encode("utf-8"), signed, hashlib.sha256).hexdigest()

    # `compare_digest`, not `==`: a byte-by-byte comparison that returns early
    # leaks where the first difference is.
    if not hmac.compare_digest(expected, provided):
        return False, "signature does not verify"

    if abs(now - int(timestamp)) > TOLERANCE_SECONDS:
        return False, f"delivery is older than {TOLERANCE_SECONDS}s"

    return True, "ok"


class Handler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
        ok, reason = verify(
            body,
            self.headers.get("X-Docdoc-Signature"),
            secret=SECRET,
            now=time.time(),
        )

        if not ok:
            # `400`, and docdoc will retry. That is the correct answer to a
            # delivery you cannot authenticate: refusing it loudly is better than
            # accepting it quietly.
            self.send_response(400)
            self.send_header("Content-Length", "0")
            self.end_headers()
            print(f"refused: {reason}", file=sys.stderr, flush=True)
            return

        payload = json.loads(body)
        # `flush`, because the documented way to run this is `… &` — output
        # redirected to a file or a pipe is block-buffered, and a receiver whose
        # whole job is showing you what arrived showed nothing until it exited.
        print(
            f"delivery {payload['delivery_id']} — run {payload['run_id']} is {payload['status']}",
            flush=True,
        )
        # Deduplicate on `delivery_id`. Delivery is at-least-once: it is stable
        # across every retry, so a receiver that records it can recognise a
        # repeat and skip the work rather than doing it twice.

        self.send_response(200)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Quiet. The lines above are the output worth having."""


def main() -> int:
    if SECRET == "whsec_change_me":
        print(
            "set WEBHOOK_SECRET to the secret you registered the callback "
            "with, or every delivery will be refused",
            file=sys.stderr,
        )

    server = HTTPServer(("0.0.0.0", PORT), Handler)
    print(
        f"listening on http://localhost:{PORT}/  (register this URL as a callback)",
        flush=True,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
