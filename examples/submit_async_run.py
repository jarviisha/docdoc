"""Submit a document without holding the connection, then poll it to completion.

    docker compose -f packaging/docker/compose.yml up -d
    docdoc migrate
    uv run python examples/submit_async_run.py

Four requests, and the shape of them is the whole lesson:

    POST /v1/documents                    -> blob_id        (the bytes come to rest)
    POST /v1/documents/{blob}/runs        -> run_id, 202    (accepted; nothing has run)
    GET  /v1/runs/{run}                   -> status         (poll until terminal)
    GET  /v1/jobs/{processing_id}/result  -> the result     (an *unchanged* route)

**The last line is the point most easily missed.** The result is not served on the
run resource. A succeeded run names its ``processing_id``, and the job route that
existed before any of this serves the result — one result representation,
reachable one way.

**A ``run_id`` is not a ``processing_id``.** A run is an *attempt* and exists from
the moment the request is accepted; a ``processing_id`` is a *result* and is the
terminal artifact's identity, so it cannot exist until the stages deriving it have
run. Submit the same document twice and you get two run ids and one processing id.
That is the answer rather than a collision — the second run reused every artifact
the first wrote and cost nothing.

Uses ``urllib`` from the standard library rather than a client package: an example
that made a reader install something before it could teach them anything would be
teaching them the wrong thing first.

**With nothing listening**, this prints the two commands that start the
composition and exits 0. That is deliberate. An example is the first thing a new
contributor runs, and a stack trace from a refused connection tells them the
example is broken when what is true is that the service is not up.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
DOCUMENT = REPO / "tests" / "fixtures" / "pdf" / "digital_invoice.pdf"
SCHEMA = "invoice@1"

#: Where the composition publishes the API. Overridable because a developer who
#: already has something on 8000 should not have to edit an example.
BASE = os.environ.get("DOCDOC_EXAMPLE_URL", "http://localhost:8000").rstrip("/")

#: A run is terminal in one of three ways, and polling stops at any of them.
TERMINAL = {"succeeded", "failed", "cancelled"}

POLL_SECONDS = 0.25
POLL_LIMIT = 240  # a minute at the interval above


def request(method: str, path: str, body: bytes | None = None) -> tuple[int, Any]:
    """One request, returning ``(status, decoded body)`` and raising for nothing.

    A 4xx is an *answer* here rather than an exception — a 404 for another
    tenant's identifier is a documented outcome, and an example that crashed on
    it would be unable to show what it looks like.
    """
    call = urllib.request.Request(f"{BASE}{path}", data=body, method=method)
    if body is not None:
        call.add_header("Content-Type", "application/pdf")
    try:
        with urllib.request.urlopen(call, timeout=30) as response:
            return response.status, json.loads(response.read() or b"null")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"null")


def reachable() -> bool:
    """Whether anything is listening, asked with the route that touches nothing."""
    try:
        status, _ = request("GET", "/healthz")
    except OSError:
        return False
    return status == 200


def main() -> int:
    if not reachable():
        print(f"nothing is listening on {BASE}. Start the composition first:\n")
        print("    docker compose -f packaging/docker/compose.yml up -d")
        print("    docdoc migrate\n")
        print("Then run this again. Set DOCDOC_EXAMPLE_URL to point elsewhere.")
        return 0

    print(f"docdoc at {BASE}")

    # 1. The bytes come to rest. `blob_id` is the hash of the document, so
    #    submitting it twice stores one copy and returns one identity.
    status, submitted = request("POST", "/v1/documents", DOCUMENT.read_bytes())
    if status != 200:
        print(f"\nsubmission refused ({status}): {submitted}")
        return 1
    blob_id = submitted["blob_id"]
    print(f"\n  blob         {blob_id}")

    # 2. Accepted, and nothing has run. This returns before any stage executes —
    #    which is the entire reason this route exists — so it is fast regardless
    #    of how long the document will take.
    started = time.monotonic()
    status, accepted = request("POST", f"/v1/documents/{blob_id}/runs?schema={SCHEMA}")
    accept_ms = (time.monotonic() - started) * 1000
    if status not in (200, 202):
        print(f"\nrun refused ({status}): {accepted}")
        print("  a 503 here means no run-state database is configured, or it is unreachable")
        return 1

    run_id = accepted["run_id"]
    print(f"  run          {run_id}")
    print(f"  accepted in  {accept_ms:.0f} ms as {accepted['status']!r}")
    # Absent, not null. A null would invite sending it to GET /v1/jobs/{id},
    # which would answer `unknown` about an identity nobody issued.
    print(f"  processing   {'absent' if 'processing_id' not in accepted else 'present (!)'}")

    # 3. Poll. A worker somewhere claimed it, executed the four stages, and wrote
    #    the terminal state back. Nothing here is holding a connection open for it.
    print("\n  polling")
    state: dict[str, Any] = {}
    for _ in range(POLL_LIMIT):
        _status, state = request("GET", f"/v1/runs/{run_id}")
        if state["status"] in TERMINAL:
            break
        time.sleep(POLL_SECONDS)
    else:
        print("    still not terminal; is a worker running?")
        return 1

    print(f"    status     {state['status']}")
    print(f"    attempts   {state['attempts']}")
    for outcome in state.get("stage_outcomes", ()):
        print(f"    {outcome['stage']:<10} {outcome['status']}")

    if state["status"] != "succeeded":
        # A failed run names the stage and the error *class* — never a message,
        # which could quote the document — and keeps the outcomes of the stages
        # that did complete. Asynchronously nobody was holding the response that
        # used to be the only place a failure existed.
        print(f"\n  failed at    {state.get('failed_stage')}")
        print(f"  error class  {state.get('error_class')}")
        return 1

    # 4. The result comes from the job route, which predates all of this.
    processing_id = state["processing_id"]
    print(f"\n  processing   {processing_id}")

    status, result = request("GET", f"/v1/jobs/{processing_id}/result")
    if status != 200:
        print(f"  result unavailable ({status})")
        return 1

    verdict = result.get("verdict")
    values = (result.get("extraction") or {}).get("values") or []
    print(f"  verdict      {verdict}")
    print(f"  values       {len(values)}")

    # And the run id and the result id are different things, which is the lesson
    # this example exists to make concrete.
    print(f"\n  run_id        {run_id}   (the attempt)")
    print(f"  processing_id {processing_id}   (the result)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
