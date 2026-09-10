"""T130a, FR-024 — installing an exporter changes no existing emission point.

An exporter that enriches a payload "harmlessly" — a trace id here, a span id
there — is how a deployment's log parsing breaks at the same moment its tracing
starts working. That failure is quiet on the tracing side, which is where
everybody is looking, and loud on the log side, where nobody has changed
anything.

So: capture the payloads with no bridge installed, install one, capture again,
and require them byte-identical. Same fields, same values, same order.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from docdoc.pipeline import observe as pipeline_observe
from docdoc.pipeline.result import StageOutcome
from docdoc.pipeline.stages import Stage
from docdoc.runs import observe as runs_observe

RUN_ID = UUID("0f8b1c2d-3e4f-4a5b-8c9d-0e1f2a3b4c5d")


@pytest.fixture(autouse=True)
def empty_slots():
    runs_observe.set_observer(None)
    pipeline_observe.set_observer(None)
    yield
    runs_observe.set_observer(None)
    pipeline_observe.set_observer(None)


def _emit_both() -> None:
    """One of each event docdoc emits, with fixed values throughout."""
    runs_observe.log_transition(
        run_id=RUN_ID,
        tenant_id="acme",
        from_state="running",
        to_state="succeeded",
        attempts=1,
        worker_id="host:1",
        reason="completed",
    )
    with pipeline_observe.correlation(request_id="req-1"):
        pipeline_observe.log_stage(
            StageOutcome(
                stage=Stage.VALIDATE,
                status="executed",
                artifact_id="sha256:" + "a" * 64,
                duration_ms=7,
            ),
            processing_id="sha256:" + "b" * 64,
        )


def _captured(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every emitted payload, as the log line and the `extra` a handler sees.

    Both, because the two modules differ in *how* they emit — one serialises to
    the message, the other passes a `docdoc` extra — and an exporter could enrich
    either one without touching the other.
    """
    out = []
    for record in caplog.records:
        payload = getattr(record, "docdoc", None)
        out.append(json.dumps(payload, sort_keys=False) if payload else record.getMessage())
    return out


def test_the_payloads_are_identical_with_and_without_a_bridge(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """**The requirement**, as one comparison."""
    with caplog.at_level("INFO"):
        _emit_both()
        before = _captured(caplog)

    caplog.clear()

    exported: list[dict] = []
    runs_observe.set_observer(exported.append)
    pipeline_observe.set_observer(exported.append)

    with caplog.at_level("INFO"):
        _emit_both()
        after = _captured(caplog)

    assert before == after, (
        "installing an observer changed what an existing emission point logs. "
        "A deployment's log parsing would break at the same moment its tracing "
        "started working (FR-024)"
    )
    assert len(exported) == 2, "and the observer did receive both events"


def test_the_observer_is_handed_the_same_mapping_that_was_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A second, narrower reading of the same rule.

    The bridge could be given a *richer* copy while the log line stayed the same,
    which would satisfy the test above and still mean two sources of truth about
    one event.
    """
    exported: list[dict] = []
    runs_observe.set_observer(exported.append)

    with caplog.at_level("INFO"):
        runs_observe.log_transition(
            run_id=RUN_ID,
            tenant_id="acme",
            from_state=None,
            to_state="queued",
            attempts=0,
            reason="submitted",
        )

    logged = json.loads(caplog.records[-1].getMessage())
    assert exported[-1] == logged


def test_this_check_can_actually_fail(caplog: pytest.LogCaptureFixture) -> None:
    """Guards the guard: a capture that saw nothing would compare `[] == []`."""
    with caplog.at_level("INFO"):
        _emit_both()

    assert len(_captured(caplog)) == 2, "both emission points were reached"
