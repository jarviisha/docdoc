"""The OTLP bridge, and the reason it does not install itself.

**A layer, and this paragraph used to say it was not.** The argument was that it
imports nothing of docdoc's, so a position would assert a dependency that does
not exist. `tests/unit/test_layer_boundaries.py` rejected it on the first run and
gave the better reason: a package on disk that no layer names is *unconstrained*,
free to import anything in any direction with CI green. What a position buys is a
constraint on **who may import this**, not an accurate statement of what it
depends on.

It shares `evaluation`'s position: below `runs`, because `api` and `runs.worker`
install the bridge, and above `pipeline`, because nothing at or below the pipeline
may reach an exporter. Constitution **v1.8.0** carries the Principle X amendment.

``pipeline/observe.py`` documents one observer slot as a decision -- "A
deployment that wants two can write a function that calls two" -- so an exporter
that called ``set_observer`` behind the operator's back would silently take that
slot from whatever was in it. This module therefore returns a **callable** and
installs nothing.

**The installing lives in `docdoc.runs.observe.install_bridge`, and the layer
graph is what put it there.** It first lived here, and `lint-imports` refused it:
deciding whether to install means reading both observer slots, one of which is
`docdoc.runs`'s, and this package sits *below* `runs`. Importing upward to
install into a slot would have inverted the direction the layers contract exists
to hold. What this module owns is the bridge; what owns the decision is the layer
that owns the slot.

**`opentelemetry` is imported inside `bridge()`**, behind ``docdoc[otel]`` (R5).
A base install neither imports nor requires it, which is what makes SC-021's
"the offline suite passes with the extra absent" true rather than asserted.
Exactly the pattern ``S3ArtifactStore.__init__`` uses for ``boto3``.

**Span attributes carry identifiers, hashes, states, counts, durations, and class
names only** (FR-020). The mapping below is written field by field for that
reason: dumping a payload wholesale would export whatever a future emission point
adds, without anybody deciding it should leave the deployment.
"""

from __future__ import annotations

import json
import logging
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = [
    "ENDPOINT_ENV",
    "HEADERS_ENV",
    "bridge",
    "configure_logging",
    "parse_headers",
    "span_for",
]

_logger = logging.getLogger("docdoc.telemetry")

#: Defined **here**, and both front ends read them from this module rather than
#: spelling them again. `docdoc.api` and `docdoc.cli` are declared independent of
#: each other, so a constant either of them owned would be one the other could
#: not import; this layer sits below both, which makes it the one place a single
#: copy can live. `docdoc.api.settings` re-exports them under the names the HTTP
#: layer's readers look for.
#:
#: **Unset means nothing is exported and no telemetry dependency is required**
#: (FR-018, SC-021).
ENDPOINT_ENV = "DOCDOC_OTLP_ENDPOINT"
HEADERS_ENV = "DOCDOC_OTLP_HEADERS"

#: The two events docdoc emits that a bridge understands. Named rather than
#: matched on a prefix, so an event added later reaches an exporter only once
#: somebody has decided which of its fields may leave the deployment.
_STAGE_EVENT = "pipeline.stage"
_TRANSITION_EVENT = "run.transition"

#: Which keys of each payload become span attributes, and **the list is the
#: policy** (FR-020, SC-015). Identifiers, hashes, states, counts, durations, and
#: class names. No `usage` — a token count is an enforcement counter and a
#: deployment's tracing backend is not where an invoice starts.
_STAGE_ATTRIBUTES = (
    "request_id",
    "processing_id",
    "step_id",
    "artifact_id",
    "outcome",
    "reused",
    "duration_ms",
    "failure_class",
    "provider",
    "model",
)
_TRANSITION_ATTRIBUTES = (
    "run_id",
    "tenant_id",
    "from_state",
    "to_state",
    "attempts",
    "worker_id",
    "reason",
)


def parse_headers(raw: str | None) -> dict[str, str]:
    """``k=v,k=v`` — the form every OTLP tool already accepts.

    Malformed pieces are dropped rather than raising: this is read at startup,
    and a service that refuses to boot over a comma in a header value has turned
    an observability misconfiguration into an outage.
    """
    headers: dict[str, str] = {}
    for piece in (raw or "").split(","):
        name, sep, value = piece.partition("=")
        if sep and name.strip():
            headers[name.strip()] = value.strip()
    return headers


def span_for(payload: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """``(span_name, attributes)`` for an event, or ``None`` to drop it.

    **A pure function, and separate from `bridge` on purpose.** What leaves the
    deployment is decided here, so SC-015 can be checked over a payload seeded
    with distinctive strings on a base install with no exporter, no collector,
    and no network — which is the only way that check runs in the offline suite
    that everybody actually runs.

    An event this bridge has not been taught is dropped rather than exported
    generically, because "generically" means "every field it happens to carry",
    and that is how a future emission point's new field leaves the deployment
    without anybody deciding it should.
    """
    event = payload.get("event")
    if event == _STAGE_EVENT:
        name, allowed = f"stage.{payload.get('step_id')}", _STAGE_ATTRIBUTES
    elif event == _TRANSITION_EVENT:
        name, allowed = f"run.{payload.get('to_state')}", _TRANSITION_ATTRIBUTES
    else:
        return None

    return name, {key: payload[key] for key in allowed if payload.get(key) is not None}


def bridge(
    *,
    endpoint: str,
    headers: Mapping[str, str] | None = None,
    tracer: Any = None,
) -> Callable[[dict[str, Any]], None]:
    """A callable the two observer slots accept. **It installs nothing.**

    Raises `ImportError` when ``docdoc[otel]`` is not installed, and that is the
    honest failure: an operator who set an endpoint asked for export, and
    returning a callable that quietly dropped every span would answer their
    request with silence.

    ``tracer`` is a seam, and a narrow one. FR-022 requires that an exporter
    failure fail no run and be reported **once per outage rather than once per
    event**, and "once per outage" is a claim about a counter that only a failing
    tracer exercises. Without this the only way to check it is a collector that
    is down, which no offline suite has — so the requirement would be asserted
    and never tested. Nothing in docdoc passes it.
    """

    # Rebound below when this function builds the provider. A caller that
    # injected a tracer owns whatever flushing it needs, so this stays a no-op
    # rather than an `AttributeError` at the bottom of the function.
    def flush(timeout_millis: int = 30_000) -> bool:
        return True

    if tracer is None:
        # Inside the function, which is the whole of R5. A module-scope import
        # here would make `docdoc.telemetry` unimportable on a base install, and
        # this package is imported by `api.app` and `runs.worker`
        # unconditionally.
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        provider = TracerProvider(resource=Resource.create({"service.name": "docdoc"}))
        provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint, headers=dict(headers or {})))
        )
        # **`provider.get_tracer`, not `trace.get_tracer`** — and the difference
        # is the whole feature.
        #
        # `trace.get_tracer(...)` reads the **global** tracer provider. This
        # function never set one, so it returned a tracer from the default
        # unconfigured provider and every span went nowhere. The `provider` built
        # two lines above — the one carrying the OTLP exporter — was constructed,
        # attached to, and then discarded. docdoc exported **zero spans**, from
        # the day telemetry landed until a collector was pointed at it and
        # observed to receive nothing.
        #
        # Nothing caught it because nothing could: the tests assert that emitting
        # does not raise, and emitting into a no-op tracer does not raise.
        #
        # And `trace.set_tracer_provider(provider)` is **not** the fix, though it
        # would also make spans arrive. That call is process-global and takes
        # effect once; using it would silently displace whatever provider the
        # deployment had already configured — exactly the displacement this
        # module refuses to perform on the observer slot, and refuses for the
        # same reason. Reading the tracer off our own provider touches no global
        # state at all.
        tracer = provider.get_tracer("docdoc")
        flush = provider.force_flush

    #: One report per outage rather than one per event (FR-022). A collector that
    #: goes away during a busy hour would otherwise write a line per stage of
    #: per run, which is a log flood caused by the thing meant to make a
    #: deployment observable.
    failing = [False]

    def _emit(payload: dict[str, Any]) -> None:
        span = span_for(payload)
        if span is None:
            return
        name, attributes = span

        try:
            with tracer.start_as_current_span(name) as started:
                for key, value in attributes.items():
                    started.set_attribute(key, value)
            failing[0] = False
        except Exception as error:
            # **No run fails and no stage blocks** (FR-022). The observer's
            # failure is not the run's; treating it as one would make
            # observability able to change an outcome, which is the one thing it
            # must never do.
            if not failing[0]:
                failing[0] = True
                _logger.warning(
                    "the telemetry exporter is failing; runs are unaffected",
                    extra={
                        "docdoc": {
                            "event": "telemetry.export_failed",
                            "error": type(error).__name__,
                        }
                    },
                )

    # **`force_flush`, hung on the callable rather than returned beside it.**
    #
    # A `BatchSpanProcessor` delivers on its own timer, so a process that emits
    # and exits promptly loses what it emitted — which is how "the bridge exports
    # nothing" stayed invisible: a test could emit, assert no exception, and end
    # before anything left. Exposing the flush is what lets a test point the
    # bridge at a collector it can read and assert that a span **arrived**, which
    # is the only assertion that would have caught the defect above.
    #
    # It is also the honest thing for a caller that is shutting down: a worker
    # stopping between batch intervals otherwise drops its last spans. Nothing in
    # docdoc calls it today, and that is a smaller gap than the one it closes.
    #
    # An attribute rather than a second return value, so every existing caller —
    # both `set_observer` slots, which take a plain callable — is unaffected.
    _emit.force_flush = flush  # type: ignore[attr-defined]
    return _emit


# ---------------------------------------------------------------------------
# Getting docdoc's own events out of the process.
#
# **They were not getting out.** Every structured event this project emits goes
# to a `docdoc.*` logger at INFO: `run.transition` from the queue,
# `credential.operation` from the audit trail, `limit.refused`,
# `retention.swept`, `delivery.attempt_failed`, and the line that says which of
# four things the OTLP bridge did. In the shipped composition, `docdoc.*` had no
# handler and inherited WARNING from a root logger that also had none — so every
# one of those records was created, formatted, and dropped.
#
# Running quickstart scenario 6 is what found it. It tells the operator to
# "check the log line on startup"; a full run had just been submitted, claimed,
# and completed, and two credentials issued, and the container's stdout carried
# nothing but uvicorn's access log. The unit tests never saw it because
# `caplog` attaches its own handler, which is exactly the thing production
# lacked.
#
# FR-035 and FR-052 say an operator MUST get a structured event. An event that
# is emitted into a logger nobody configured satisfies the letter and none of
# the intent.
# ---------------------------------------------------------------------------

#: The logger every `docdoc.*` logger descends from, so one configuration covers
#: the whole project without touching the root logger.
ROOT_LOGGER = "docdoc"


class _JsonLines(logging.Formatter):
    """One JSON object per record, carrying the structured payload.

    A plain formatter prints `record.getMessage()` and discards
    ``extra={"docdoc": …}`` — which is where most of these events keep their
    content, so the operator would get the word `credential.operation` and none
    of the actor, operation, tenant, or identifier that make it an audit trail.

    Two shapes are handled because the project emits two: `runs/observe.py`
    serialises its payload into the message, and everything else passes an
    `extra`. Both come out as one line of JSON here, which is what a log
    aggregator wants and what `docs/concepts/runs.md` shows.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = getattr(record, "docdoc", None)
        message = record.getMessage()

        if payload is None:
            # `runs/observe.py` already produced JSON; anything else is prose.
            try:
                parsed = json.loads(message)
            except (ValueError, TypeError):
                parsed = {"message": message}
            payload = parsed if isinstance(parsed, dict) else {"message": message}

        line = {"level": record.levelname, "logger": record.name, **payload}
        if record.exc_info:
            # The class, never the traceback's quoted locals — the same rule
            # every observer here follows about not carrying content.
            line["error"] = record.exc_info[0].__name__ if record.exc_info[0] else None
        return json.dumps(line, default=str)


def configure_logging(level: int = logging.INFO) -> str:
    """Make docdoc's structured events visible, without displacing a deployment's.

    Returns what it did — ``"configured"``, ``"root"``, or ``"installed"`` — for
    the same reason `install_bridge` does: an operator who sees no events should
    be able to find out why without guessing.

    **It defers to whatever is already there**, which is the principle the
    observer slot establishes and this follows:

    * a `docdoc` logger that already has a handler belongs to the deployment, and
      this returns without touching it;
    * a root logger with handlers means the deployment configured logging
      centrally, so this changes **nothing at all** and lets their configuration
      decide — including their levels, even if that means docdoc's INFO events
      stay filtered. Their logging, their call; the return value says it
      deferred;
    * only when neither is true — which is the composition, and `uvicorn`, and a
      bare `docdoc worker` — does it set a level and attach a handler.

    **The middle case used to lift the level anyway, and that was wrong twice
    over.** It mutated a process-global logger on behalf of a deployment that had
    already made its own arrangements, and because the mutation outlives the
    call, it leaked: under `pytest` — where the root logger always has a handler
    — one test that built an app raised `docdoc` to INFO for every test after it,
    and a test asserting "exactly one event" started seeing three. A function
    that quietly changes global state for later callers is the thing this
    project keeps refusing to do to the observer slot.

    Idempotent, so calling it from both front ends is safe.
    """
    logger = logging.getLogger(ROOT_LOGGER)

    if logger.handlers:
        return "configured"

    if logging.getLogger().handlers:
        return "root"

    logger.setLevel(level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonLines())
    logger.addHandler(handler)
    # Not to the root logger's handlers as well: this one already formats them
    # as JSON, and a second copy in a different shape is how one event becomes
    # two facts that disagree.
    logger.propagate = False
    return "installed"
