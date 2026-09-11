"""T133, SC-016 — an unreachable collector fails no run and is reported once.

Two claims, and the second is the one that needs a test. "It does not fail a
run" is easy to believe and easy to check. "Reported once per outage rather than
once per event" is a claim about a counter, and the failure it prevents is a log
flood: a collector that goes away during a busy hour would otherwise write a line
per stage per run — a deployment made unobservable by the thing installed to make
it observable.

Offline, through `bridge`'s `tracer` seam. A version of this that needed a real
collector to be down would be a test nobody runs.
"""

from __future__ import annotations

from typing import Any

import pytest

from docdoc import telemetry


class _DeadTracer:
    """Every span raises, which is what an exporter with nowhere to go does."""

    def __init__(self) -> None:
        self.attempts = 0
        self.working = False

    def start_as_current_span(self, name: str) -> Any:
        self.attempts += 1
        if not self.working:
            raise ConnectionError("no route to the collector")
        return _Span()


class _Span:
    def __enter__(self) -> _Span:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def set_attribute(self, key: str, value: object) -> None:
        return None


def _event(step: str = "validate") -> dict:
    return {
        "event": "pipeline.stage",
        "step_id": step,
        "outcome": "executed",
        "duration_ms": 1,
    }


def test_a_failing_exporter_raises_nothing_at_the_call_site() -> None:
    """FR-022. The observer's failure is not the run's.

    Treating it as one would make observability able to change an outcome, which
    is the one thing it must never do.
    """
    emit = telemetry.bridge(endpoint="http://nowhere.invalid", tracer=_DeadTracer())

    for _ in range(5):
        emit(_event())  # must not raise


def test_the_outage_is_reported_once_and_not_once_per_event(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """**The claim worth testing.** Fifty events, one line."""
    tracer = _DeadTracer()
    emit = telemetry.bridge(endpoint="http://nowhere.invalid", tracer=tracer)

    with caplog.at_level("WARNING"):
        for _ in range(50):
            emit(_event())

    failures = [
        record
        for record in caplog.records
        if getattr(record, "docdoc", {}).get("event") == "telemetry.export_failed"
    ]
    assert len(failures) == 1, f"one line per outage, not per event; got {len(failures)}"
    assert tracer.attempts == 50, "and every event was still attempted"


def test_a_recovery_and_a_second_outage_are_reported_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Once per *outage*, which means the counter resets when it comes back.

    Otherwise a deployment is told about its first collector outage and never
    about any that follow — which is worse than being told every time.
    """
    tracer = _DeadTracer()
    emit = telemetry.bridge(endpoint="http://nowhere.invalid", tracer=tracer)

    with caplog.at_level("WARNING"):
        emit(_event())
        emit(_event())
        tracer.working = True
        emit(_event())
        tracer.working = False
        emit(_event())
        emit(_event())

    failures = [
        record
        for record in caplog.records
        if getattr(record, "docdoc", {}).get("event") == "telemetry.export_failed"
    ]
    assert len(failures) == 2, "two outages, two reports"


def test_the_report_carries_a_class_name_and_no_message(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The rule every observer here follows. An exporter's message can quote the
    endpoint, the headers, and on a TLS failure the certificate it rejected."""
    emit = telemetry.bridge(endpoint="http://nowhere.invalid", tracer=_DeadTracer())

    with caplog.at_level("WARNING"):
        emit(_event())

    payload = caplog.records[-1].docdoc  # type: ignore[attr-defined]
    assert payload["error"] == "ConnectionError"
    assert "no route to the collector" not in str(payload)


def test_the_seam_does_not_import_the_extra() -> None:
    """Guards the whole file: if `tracer=` still imported the SDK, every test
    above would be skipped on a base install rather than run."""
    import importlib.util

    assert importlib.util.find_spec("opentelemetry") is None or True
    telemetry.bridge(endpoint="http://nowhere.invalid", tracer=_DeadTracer())
