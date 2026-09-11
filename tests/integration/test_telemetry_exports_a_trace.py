"""T181, SC-014, FR-021 — a run reaches a collector, correlatable both ways.

**The `otel` marker existed and marked nothing.** `pyproject.toml` registered it
in Phase 1 "so the offline suite excludes exporter-dependent tests by default",
`tests/infra.py` defined `DOCDOC_TEST_OTLP_ENDPOINT`, and no test in the
repository carried either. So the first half of SC-014 — *with an exporter
configured, 100% of executed runs produce a trace correlatable to a run* — was
asserted by nothing, and a marker that marks nothing is the same defect as a
documented variable nothing reads. This file is the test the marker was
registered for.

`test_telemetry_leaks_nothing.py` and `test_telemetry_collector_down.py` cover
what a span may carry and what happens when the collector is gone, both offline
and both over `span_for` and the `tracer` seam. What neither can cover is the
part that only a real SDK exercises: that `bridge()` builds, that a payload
survives the exporter's own serialisation, and that both identities arrive.

Skips with a reason naming what to start when either the extra or the collector
is missing, which is the `provider` pattern this suite has used since Milestone 3.
"""

from __future__ import annotations

import pytest
from tests.infra import require_otlp_endpoint

from docdoc.pipeline import observe as pipeline_observe
from docdoc.runs import observe as runs_observe

pytestmark = pytest.mark.otel

RUN_ID = "0f8b1c2d-3e4f-4a5b-8c9d-0e1f2a3b4c5d"
PROCESSING_ID = "sha256:" + "b" * 64


@pytest.fixture
def endpoint() -> str:
    return require_otlp_endpoint()


@pytest.fixture(autouse=True)
def empty_slots():
    runs_observe.set_observer(None)
    pipeline_observe.set_observer(None)
    yield
    runs_observe.set_observer(None)
    pipeline_observe.set_observer(None)


def test_the_bridge_builds_against_the_real_sdk(endpoint: str) -> None:
    """The import that a base install must not perform, performed.

    Everything offline substitutes a tracer, so this is the only place the
    lazy import inside `bridge()` is actually executed — and the only place a
    rename in `opentelemetry-sdk` would be caught.
    """
    from docdoc.telemetry import bridge

    emit = bridge(endpoint=endpoint)

    assert callable(emit)


def test_a_stage_event_and_a_transition_both_export(endpoint: str) -> None:
    """**SC-014.** Both emission points reach the exporter without raising.

    Spans leave asynchronously through a batch processor, so what is asserted
    here is that docdoc's side completes — the collector's own contents are the
    operator's to inspect, and a test that polled a collector's API would be
    testing the collector.
    """
    from docdoc.telemetry import bridge

    emit = bridge(endpoint=endpoint)
    runs_observe.set_observer(emit)
    pipeline_observe.set_observer(emit)

    runs_observe.log_transition(
        run_id=__import__("uuid").UUID(RUN_ID),
        tenant_id="acme",
        from_state="running",
        to_state="succeeded",
        attempts=1,
        worker_id="host:1",
        reason="completed",
    )

    from docdoc.pipeline.result import StageOutcome
    from docdoc.pipeline.stages import Stage

    with pipeline_observe.correlation(request_id="req-1"):
        pipeline_observe.log_stage(
            StageOutcome(
                stage=Stage.VALIDATE,
                status="executed",
                artifact_id=PROCESSING_ID,
                duration_ms=7,
            ),
            processing_id=PROCESSING_ID,
        )


def test_both_identities_reach_the_span(endpoint: str) -> None:
    """**FR-021.** Correlatable to a *run* by run identity, and to a *result* by
    processing identity — the two questions an operator actually asks.

    Checked on what `span_for` selects rather than by reading the collector,
    because that mapping is the whole of what docdoc controls; the exporter's job
    after that is somebody else's.
    """
    from docdoc.telemetry import span_for

    _, transition = span_for(
        {
            "event": "run.transition",
            "run_id": RUN_ID,
            "tenant_id": "acme",
            "to_state": "succeeded",
            "attempts": 1,
        }
    )
    assert transition["run_id"] == RUN_ID

    _, stage = span_for(
        {
            "event": "pipeline.stage",
            "step_id": "validate",
            "processing_id": PROCESSING_ID,
            "outcome": "executed",
        }
    )
    assert stage["processing_id"] == PROCESSING_ID


def test_a_run_that_exports_still_succeeds(endpoint: str) -> None:
    """SC-016's other direction: exporting changes no outcome.

    The bridge is installed in both slots and a full transition sequence runs
    through it. Nothing here may raise, because an observer that could fail a run
    would make observability able to change an outcome.
    """
    from uuid import uuid4

    from docdoc.telemetry import bridge

    emit = bridge(endpoint=endpoint)
    runs_observe.set_observer(emit)

    run_id = uuid4()
    for from_state, to_state, reason in (
        (None, "queued", "submitted"),
        ("queued", "running", "claimed"),
        ("running", "succeeded", "completed"),
    ):
        runs_observe.log_transition(
            run_id=run_id,
            tenant_id="acme",
            from_state=from_state,
            to_state=to_state,
            attempts=1,
            reason=reason,
        )


# -- the assertion that would have caught the bridge exporting nothing --------
#
# Everything above asserts that emitting does not raise. That is necessary and it
# is not sufficient: for the whole life of this feature the bridge took its
# tracer from the *global* provider rather than the one it had just attached an
# exporter to, so every span went to a no-op tracer. Emitting into a no-op tracer
# does not raise, so all four tests above passed while docdoc exported nothing.
#
# These two point the bridge at a collector they can read.


def test_a_span_actually_reaches_a_collector() -> None:
    """**SC-014, and the only form of it worth having.**

    Not "the exporter was configured" and not "emitting did not raise" — a batch
    arrived at an HTTP endpoint. Needs no `DOCDOC_TEST_OTLP_ENDPOINT`, because it
    brings its own collector; it still needs the extra, which is what the `otel`
    mark selects for.
    """
    import importlib.util
    from uuid import uuid4

    if importlib.util.find_spec("opentelemetry") is None:
        pytest.skip("the `otel` extra is not installed; `uv sync --all-extras`")

    from tests.support.otlp_collector import OtlpCollector

    from docdoc.telemetry import bridge

    with OtlpCollector() as collector:
        emit = bridge(endpoint=collector.endpoint)
        runs_observe.set_observer(emit)

        runs_observe.log_transition(
            run_id=uuid4(),
            tenant_id="acme",
            from_state="running",
            to_state="succeeded",
            attempts=1,
            reason="completed",
        )

        # A `BatchSpanProcessor` delivers on its own timer, so without this the
        # test would end before the span left and would pass for the wrong
        # reason — which is the failure mode it exists to prevent.
        assert emit.force_flush(timeout_millis=10_000)

        assert collector.received >= 1, (
            "no span reached the collector. The bridge is configured and emitting "
            "raises nothing, which is exactly what a tracer from the *global* "
            "provider does — check that `bridge()` reads its tracer off the "
            "provider it built (SC-014)"
        )


def test_the_bridge_does_not_touch_the_global_tracer_provider() -> None:
    """The other half, and the reason the fix is not `set_tracer_provider`.

    That call is process-global and takes effect once, so using it would silently
    displace whatever provider the deployment had configured — the same
    displacement this module refuses to perform on the observer slot, and refused
    for the same reason (research R4).
    """
    import importlib.util

    if importlib.util.find_spec("opentelemetry") is None:
        pytest.skip("the `otel` extra is not installed; `uv sync --all-extras`")

    from opentelemetry import trace
    from tests.support.otlp_collector import OtlpCollector

    from docdoc.telemetry import bridge

    before = trace.get_tracer_provider()

    with OtlpCollector() as collector:
        bridge(endpoint=collector.endpoint)

    assert trace.get_tracer_provider() is before, (
        "building a bridge replaced the global tracer provider, which would take "
        "an exporter away from whatever the deployment had already configured"
    )
