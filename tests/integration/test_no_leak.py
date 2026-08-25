"""What a run says about itself, and what it must never say.

The constitution requires both halves at MVP: structured logging with request id,
processing id, step id, latency, provider, model, and token usage — *and* a
prohibition on logging document contents, PII, API keys, or prompts. This asserts
both, because either alone is the wrong system. Logs with no fields cannot answer
an operational question; logs with the document in them are a second copy of the
document in whatever store the logs go to.

**One fixture and one distinctive string serve every surface.** FR-042 names six
places a value must not appear, and splitting them into six tests would duplicate
the setup six times while testing the same property. What differs between them is
only where you look.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from docdoc.artifacts import FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.pipeline import observe, run

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"

#: Strings that exist in this document and in nothing else about the run. Finding
#: any of them in a log is unambiguous.
FROM_THE_DOCUMENT = ("INV-001", "ACME LTD", "1,240.00", "Widget, large")

#: An extracted value, which is a different category from the document's text and
#: is prohibited just as firmly.
EXTRACTED = ("Acme Ltd", "1240.00", "USD")

CREDENTIAL = "sk-test-do-not-log-me-0123456789"


@pytest.fixture
def captured(caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch) -> Any:
    """Everything docdoc logs during one run, with a credential in the environment."""
    monkeypatch.setenv("GEMINI_API_KEY", CREDENTIAL)
    caplog.set_level(logging.DEBUG, logger="docdoc")
    return caplog


def _run(store: FileArtifactStore | None = None, **kwargs: Any) -> Any:
    return run(
        FIXTURE.read_bytes(),
        schema=SCHEMA,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        store=store,
        **kwargs,
    )


def _everything_logged(caplog: pytest.LogCaptureFixture) -> str:
    """Every message *and* every structured field, flattened to one string.

    Both halves matter. Asserting on messages alone would miss a value smuggled
    into ``extra``, which is exactly where a well-meaning debugging change would
    put it.
    """
    parts: list[str] = []
    for record in caplog.records:
        parts.append(record.getMessage())
        for key, value in record.__dict__.items():
            if key not in logging.LogRecord("", 0, "", 0, "", None, None).__dict__:
                parts.append(f"{key}={value!r}")
    return "\n".join(parts)


def test_the_logs_carry_every_required_field(captured: pytest.LogCaptureFixture) -> None:
    """The half that is easy to forget once the prohibition is satisfied.

    A log that leaks nothing because it says nothing satisfies FR-043 and defeats
    FR-045, and the two are one requirement between them.
    """
    events: list[dict[str, Any]] = []
    observe.set_observer(events.append)
    try:
        result = _run(request_id="req-under-test")
    finally:
        observe.set_observer(None)

    assert len(events) == 4, "one event per stage (FR-045)"
    for event in events:
        assert event["request_id"] == "req-under-test"
        assert event["step_id"] in {"parse", "extract", "ground", "validate"}
        assert event["duration_ms"] >= 0
        assert event["outcome"]
        assert event["processing_id"] == result.processing_id

    extract_event = next(event for event in events if event["step_id"] == "extract")
    assert extract_event["provider"] == "echo", "FR-046: which provider answered"
    assert extract_event["model"], "FR-046: which model answered"
    assert "usage" in extract_event, "FR-046: what it cost in tokens"


def test_no_document_content_or_value_or_credential_reaches_the_logs(
    captured: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """SC-008 — 0% of these strings, across a whole run with a store."""
    _run(store=FileArtifactStore(tmp_path))
    logged = _everything_logged(captured)

    for needle in FROM_THE_DOCUMENT:
        assert needle not in logged, f"document text {needle!r} reached a log"
    for needle in EXTRACTED:
        assert needle not in logged, f"extracted value {needle!r} reached a log"
    assert CREDENTIAL not in logged, "a credential reached a log"


def test_a_failed_run_leaks_nothing_either(captured: pytest.LogCaptureFixture) -> None:
    """The path where a message is most tempting: something went wrong.

    A failure event carries the error's **class**, never its message, because a
    message can quote the content it choked on.
    """
    result = _run()
    assert result.failed_stage is None

    failed = run(
        FIXTURE.read_bytes(),
        schema="no-such-schema@1",
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
    )
    assert failed.failed_stage is not None

    logged = _everything_logged(captured)
    for needle in (*FROM_THE_DOCUMENT, *EXTRACTED, CREDENTIAL):
        assert needle not in logged


def test_a_credential_never_enters_an_identity(
    captured: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-042 — and this one cannot be checked by searching a string.

    An identity is a hash, so a credential folded into one would be invisible to
    every leak test in this file. It is checked by *moving* the credential: if it
    had entered an identity, the identity would move with it.
    """
    first = _run()

    monkeypatch.setenv("GEMINI_API_KEY", "a-completely-different-credential")
    second = _run()

    assert first.processing_id == second.processing_id, (
        "the run identity moved when a credential changed, so the credential is "
        "folded into it (FR-042)"
    )


def test_a_stored_artifact_carries_no_credential(
    captured: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """Artifacts hold extracted values by nature; they must hold nothing else.

    The document's own text and its extracted values are *expected* here — that
    is what an artifact is — so this asserts the one category that is never
    legitimate.
    """
    _run(store=FileArtifactStore(tmp_path))

    for entry in (tmp_path / "artifacts").glob("*/*.json"):
        assert CREDENTIAL not in entry.read_text()


def test_the_error_body_of_a_failed_run_carries_no_provider_message() -> None:
    """FR-037 — a provider's error text may quote the document it choked on."""
    pytest.importorskip("fastapi")
    from docdoc.api.errors import body_for_failed_run

    failed = run(
        FIXTURE.read_bytes(),
        schema="no-such-schema@1",
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
    )
    serialised = json.dumps(body_for_failed_run(failed).model_dump(mode="json"))

    for needle in FROM_THE_DOCUMENT:
        assert needle not in serialised
    assert CREDENTIAL not in serialised


def test_observability_changes_no_result_no_identity_and_no_verdict(
    captured: pytest.LogCaptureFixture,
) -> None:
    """FR-049 — the requirement that makes the rest of this file safe to add to."""
    quiet = _run()

    events: list[dict[str, Any]] = []
    observe.set_observer(events.append)
    try:
        loud = _run(request_id="noisy")
    finally:
        observe.set_observer(None)

    assert events, "the observer was not called, so this compares nothing"
    assert quiet.processing_id == loud.processing_id
    assert quiet.validation == loud.validation
    assert quiet.extraction == loud.extraction
    assert quiet.grounding == loud.grounding


def test_an_observer_that_raises_does_not_fail_the_run(
    captured: pytest.LogCaptureFixture,
) -> None:
    """A tracing exporter is an outer concern, and outer concerns do not get a
    vote on whether a run succeeded."""

    def explode(_: dict[str, Any]) -> None:
        raise RuntimeError("the exporter is down")

    observe.set_observer(explode)
    try:
        result = _run()
    finally:
        observe.set_observer(None)

    assert result.failed_stage is None
    assert result.validation is not None


# -- FR-045 on the path that raises ------------------------------------------


def _artifact_for(root: Path, stage: Any) -> Path:
    """The stored envelope for one named stage.

    **Chosen by name, never by filesystem order.** These tests used to reach for
    `next(glob("*/*.json"))`, which corrupts whichever entry the platform happens
    to list first — the store's two-character fan-out makes that order a property
    of the filesystem rather than of the run. On ext4 here it returned the
    `validate` artifact and the tests passed; on GitHub's ubuntu and windows
    runners it returned `parse`, the run died at the first stage having emitted
    nothing, and the assertion below failed. Green on one machine through six
    convergence passes for no better reason than directory iteration order.
    """
    entries = sorted((root / "artifacts").glob("*/*.json"))
    for entry in entries:
        if json.loads(entry.read_text())["stage"] == stage.value:
            return entry
    raise AssertionError(
        f"no {stage.value} artifact under {root}; the fixture stored "
        f"{[json.loads(e.read_text())['stage'] for e in entries]}"
    )


def test_a_run_that_raises_still_emits_events_for_the_stages_that_ran(
    captured: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """FR-045 — one event per stage *execution*, including a run that never returns.

    This regressed silently for a whole milestone. ``_emit`` sat only on the
    return path, so a ``PipelineError`` or ``ArtifactError`` propagating produced
    **zero** ``pipeline.stage`` events even though earlier stages had executed —
    and the runs most worth having events for are disproportionately the ones
    that failed. An operator whose store has gone bad wants to know how far the
    run got before it noticed.

    Provoked with a corrupted stored payload, which is the real way this happens:
    the store raises rather than degrading, because a `content_id` mismatch is
    corruption and recomputing over it would hide a failing disk behind a slower
    run (FR-014).
    """
    from docdoc.artifacts import ArtifactError
    from docdoc.pipeline import Stage

    store = FileArtifactStore(tmp_path)
    first = _run(store=store)
    assert first.failed_stage is None, "the fixture must succeed before it is corrupted"

    # Corrupting the *last* stage is the strongest version of this check: every
    # earlier stage is then reused from the store, so all three must still emit
    # an event despite executing nothing. "One event per stage execution" is the
    # weaker reading of FR-045; this pins the stronger one.
    entry = _artifact_for(tmp_path, Stage.VALIDATE)
    stored = json.loads(entry.read_text())
    stored["payload"]["__corrupted_by_this_test__"] = True
    entry.write_text(json.dumps(stored))

    events: list[dict[str, Any]] = []
    observe.set_observer(events.append)
    try:
        with pytest.raises(ArtifactError):
            _run(store=store)
    finally:
        observe.set_observer(None)

    assert events, (
        "a run that raised emitted no stage events at all. The stages before the "
        "failure did execute, and FR-045 asks for one event each."
    )
    for event in events:
        assert event["step_id"] in {stage.value for stage in Stage}
        assert event["duration_ms"] >= 0
        # No terminal artifact exists, so there is no processing id to carry.
        # Recorded as absent rather than as a placeholder: a reader can tell
        # "not yet" from "not applicable", and a synthesised value would be a
        # second run identifier (FR-007).
        assert event["processing_id"] is None

    steps = [event["step_id"] for event in events]
    assert steps == sorted(set(steps), key=steps.index), "a stage was reported twice"
    assert len(steps) < 4, "the run raised, so it cannot have completed every stage"


def test_a_raising_run_leaks_nothing_through_its_events(
    captured: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    """The events added above must obey the same prohibition as the rest."""
    from docdoc.artifacts import ArtifactError
    from docdoc.pipeline import Stage

    store = FileArtifactStore(tmp_path)
    _run(store=store)

    entry = _artifact_for(tmp_path, Stage.VALIDATE)
    stored = json.loads(entry.read_text())
    stored["payload"]["__corrupted_by_this_test__"] = True
    entry.write_text(json.dumps(stored))

    with pytest.raises(ArtifactError):
        _run(store=store)

    logged = _everything_logged(captured)
    for needle in (*FROM_THE_DOCUMENT, *EXTRACTED, CREDENTIAL):
        assert needle not in logged
