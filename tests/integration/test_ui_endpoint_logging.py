"""What the two new endpoints say about themselves, and what they must never say.

The constitution requires both halves at MVP: structured logging with request id,
processing id, step id, latency, provider, model, and token usage — *and* a
prohibition on logging document contents, PII, credentials, or prompts. Milestone
7 asserts both for the library and for its five endpoints; this asserts them for
the two Milestone 8 adds.

**Why a separate file rather than a line in ``test_no_leak.py``.** The prohibition
is not a property of the pipeline alone, it is a property of every surface that
can reach the pipeline. ``POST /v1/extract`` is a new such surface — it takes raw
bytes and runs a document through four stages — and a new surface is exactly where
redaction is lost, because the code that would leak is code nobody has written
yet. This file exists so that writing it fails.

That this test was missing entirely until `/speckit-analyze` found it is recorded
in ``tasks.md``: FR-033 and SC-009 reached the task list with no task at all, on
the milestone that added the surface.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"

#: Strings that exist in this document and in nothing else about the run, so
#: finding one in a log is unambiguous. Same set as ``test_no_leak.py`` uses,
#: deliberately: a second list would drift from the first.
FROM_THE_DOCUMENT = ("INV-001", "ACME LTD", "1,240.00", "Widget, large")

#: Extracted values — a different category from the document's text, prohibited
#: just as firmly, and the category this endpoint returns in its response body.
EXTRACTED = ("Acme Ltd", "1240.00", "USD")

CREDENTIAL = "sk-test-do-not-log-me-0123456789"


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("GEMINI_API_KEY", CREDENTIAL)
    return TestClient(
        build_app(
            _Deployment(
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
            )
        )
    )


@pytest.fixture
def captured(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger="docdoc")
    return caplog


def _logged(captured: pytest.LogCaptureFixture) -> str:
    return "\n".join(
        record.getMessage() + " " + repr(getattr(record, "__dict__", {}))
        for record in captured.records
    )


def test_a_storeless_run_logs_no_document_content(
    client: TestClient, captured: pytest.LogCaptureFixture
) -> None:
    """FR-033, SC-009 — the document is not a second copy of itself in the logs."""
    response = client.post("/v1/extract", params={"schema": SCHEMA}, content=FIXTURE.read_bytes())
    assert response.status_code == 200

    logs = _logged(captured)
    for needle in FROM_THE_DOCUMENT:
        assert needle not in logs, f"the document's text reached a log: {needle!r}"


def test_a_storeless_run_logs_no_extracted_value(
    client: TestClient, captured: pytest.LogCaptureFixture
) -> None:
    """FR-033 — the values are the caller's own document coming back.

    Returning them on the caller's own response is a different thing from writing
    them wherever the logs go, which is the distinction FR-043 of Milestone 7
    draws and this endpoint must keep.
    """
    body = client.post("/v1/extract", params={"schema": SCHEMA}, content=FIXTURE.read_bytes()).text
    logs = _logged(captured)

    for needle in EXTRACTED:
        assert needle in body, f"{needle!r} should be in the response — check the fixture"
        assert needle not in logs, f"an extracted value reached a log: {needle!r}"


def test_no_credential_reaches_a_log(
    client: TestClient, captured: pytest.LogCaptureFixture
) -> None:
    """FR-033 — a key in the environment stays there."""
    client.post("/v1/extract", params={"schema": SCHEMA}, content=FIXTURE.read_bytes())

    assert CREDENTIAL not in _logged(captured)


def test_a_failing_run_logs_no_document_content_either(
    client: TestClient, captured: pytest.LogCaptureFixture
) -> None:
    """The path where redaction is usually lost.

    Error handling is where a document gets attached to a message "for
    debugging", and a failure is the moment nobody is looking at the log format.
    """
    response = client.post(
        "/v1/extract", params={"schema": "no-such-schema@1"}, content=FIXTURE.read_bytes()
    )
    assert response.status_code >= 400

    logs = _logged(captured)
    for needle in FROM_THE_DOCUMENT:
        assert needle not in logs, f"a failing run logged the document: {needle!r}"


def test_the_schema_listing_logs_no_content(
    client: TestClient, captured: pytest.LogCaptureFixture
) -> None:
    """FR-033 — the quieter endpoint, checked anyway.

    It reads no document, so there is nothing it *should* leak; asserting that
    costs one call and closes the surface rather than reasoning about it.
    """
    assert client.get("/v1/schemas").status_code == 200

    logs = _logged(captured)
    for needle in (*FROM_THE_DOCUMENT, *EXTRACTED, CREDENTIAL):
        assert needle not in logs


def test_the_storeless_route_emits_the_existing_stage_events_and_no_others(
    client: TestClient,
) -> None:
    """T019, FR-033 — the other half, and the reason this endpoint added no logging.

    A log that leaks nothing because it says nothing satisfies the prohibition and
    defeats the observability requirement; the two are one requirement between
    them. So this asserts the storeless route produces exactly the four stage
    events the pipeline already emits, with the caller's request id threaded
    through — meaning it reuses that path rather than growing a second one.

    A second logging path is how the first one's redaction stops being the only
    one that matters, and it would be invisible to every test above: they check
    what is *not* said, and a new emitter is a new place to say it.
    """
    from docdoc.pipeline import observe

    events: list[dict[str, object]] = []
    observe.set_observer(events.append)
    try:
        response = client.post(
            "/v1/extract",
            params={"schema": SCHEMA},
            content=FIXTURE.read_bytes(),
            headers={"x-request-id": "req-storeless"},
        )
    finally:
        observe.set_observer(None)

    assert response.status_code == 200
    assert len(events) == 4, "one event per stage, and not one more"
    for event in events:
        assert event["request_id"] == "req-storeless"
        assert event["step_id"] in {"parse", "extract", "ground", "validate"}
        assert event["outcome"]
