"""One event per run state transition, and no summary.

`pipeline/observe.py` refuses a run-level event and gives its reason:

    A fifth event summarising the four would be a second place where the cost of
    a run is stated, and the two would eventually disagree.

That objection is against a **summary**, and a transition event is not one. It
carries identities, states, an attempt count, and a reason; it states no
duration, no token count, no cost, and no stage result. The per-stage events
already say what a run cost, exactly once.

**What changed is that asynchrony moved real events outside every stage.** A
claim, a lease expiry, a redelivery, a cancellation, an abandonment — none has a
stage to attach to. And under FR-091 a run can fail without reaching a stage at
all, emitting no `pipeline.stage` event whatever. Leaving this to Milestone 10
would ship a four-process topology in which lease handoff between workers, the
hardest thing in it to debug, is the only thing that logs nothing.

**This is not OpenTelemetry arriving early.** Standard-library `logging`, like
the five `observe.py` modules that already exist. Milestone 10 binds an exporter;
what this milestone gives it is something to bind to, so that adding an emission
point and an exporter are not one change with two ways to be wrong.

**What never appears**: document text, extracted values, claimed text, prompt
bodies, credentials, or a provider's error message (FR-093). Identifiers, states,
counts, and class names only — the same rule every other observer here follows.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

__all__ = [
    "EVENT_NAME",
    "REASONS",
    "install_bridge",
    "log_transition",
    "observer",
    "reason_for",
    "set_observer",
]

EVENT_NAME = "run.transition"

#: A deployment's bridge to whatever it uses. **One slot**, mirroring
#: `pipeline/observe.py` exactly — same signature, return value ignored, a
#: raising observer cannot fail a run — so a deployment learns one pattern rather
#: than two that are nearly the same.
#:
#: It did not exist until Milestone 10, and its absence was not cosmetic: this
#: module only called `logging`, so `run.transition` could not reach an exporter
#: at all. FR-017 asks that a transition be visible in an operator's tracing
#: backend, and a requirement cannot bind to something that does not exist
#: (research R4).
_OBSERVER: list[Callable[[dict[str, Any]], None]] = []


def set_observer(callback: Callable[[dict[str, Any]], None] | None) -> None:
    """Install, or remove, the span bridge.

    One observer, not a list. The argument is `pipeline/observe.py`'s and is
    inherited rather than restated: a deployment that wants two can write a
    function that calls two, and a registry of subscribers would be an event bus
    — infrastructure with no present-tense reason to exist (Principle XI).

    **Nothing installs this on its own behalf.** `docdoc.telemetry` returns a
    bridge and does not call this; the API and the worker install it, and only
    when the slot is empty, so an exporter cannot silently take a slot from
    whatever a deployment already had in it.
    """
    _OBSERVER.clear()
    if callback is not None:
        _OBSERVER.append(callback)


def observer() -> Callable[[dict[str, Any]], None] | None:
    """The installed bridge, if any. What the "is the slot empty?" check reads."""
    return _OBSERVER[0] if _OBSERVER else None


def install_bridge(
    build: Callable[[], Callable[[dict[str, Any]], None]] | None,
    *,
    endpoint: str | None,
) -> str:
    """Install an exporter into both observer slots, if both are empty.

    Returns ``"unconfigured"``, ``"installed"``, ``"occupied"``, or
    ``"unavailable"``, and **logs which, once** — each of the four is otherwise
    something an operator has to infer from an absence of traces, which is the
    hardest thing there is to debug.

    **`build` is passed in rather than imported, and the layer graph is why.**
    Three arrangements were tried. Putting this whole function in
    `docdoc.telemetry` failed because deciding whether to install means reading
    *this* module's slot, and `telemetry` sits below `runs`. Importing
    `docdoc.telemetry` from here failed differently and more usefully: the
    "outbound HTTP is confined" contract enumerates this package's modules, and
    `postgres` imports `observe`, so the exporter became reachable from the queue
    through two hops. Both refusals are the contracts working.

    What is left is the arrangement that was true all along — the *front end*
    composes them. `docdoc.api.app` and `docdoc.cli.commands.worker` each hand
    this `telemetry.bridge`; this module owns the policy and knows nothing about
    what an exporter is, and `telemetry` owns the exporter and knows nothing
    about slots.

    **The slot check is the point** (research R4). `pipeline/observe.py` argues
    the single slot as a decision, so an exporter that called `set_observer`
    unconditionally would take it from whatever a deployment had installed —
    making "tracing started working" mean "somebody else's observability
    stopped". Declining on *either* slot being occupied is deliberate: filling
    the empty one would leave a deployment with half its events exported and no
    way to notice which half.
    """
    from docdoc.pipeline import observe as pipeline_observe

    if not (endpoint or "").strip() or build is None:
        return "unconfigured"

    occupied = [
        where
        for where, current in (
            ("pipeline", pipeline_observe.observer()),
            ("runs", observer()),
        )
        if current is not None
    ]
    if occupied:
        _logger.info(
            "an observer is already installed; the OTLP bridge was not installed",
            extra={"docdoc": {"event": "telemetry.not_installed", "occupied": occupied}},
        )
        return "occupied"

    try:
        emit = build()
    except ImportError:
        # An operator who set an endpoint asked for export. Answering that with
        # silence would be the worst outcome; refusing to boot would be nearly as
        # bad, for a capability no run depends on.
        _logger.warning(
            "an OTLP endpoint is configured but the exporter is not installed",
            extra={"docdoc": {"event": "telemetry.unavailable", "extra": "docdoc[otel]"}},
        )
        return "unavailable"

    pipeline_observe.set_observer(emit)
    set_observer(emit)
    _logger.info("the OTLP bridge is installed", extra={"docdoc": {"event": "telemetry.installed"}})
    return "installed"


def _notify(payload: dict[str, Any]) -> None:
    """Hand the event to the deployment's bridge, and survive it.

    A tracing exporter that raises must not fail a run whose transition has
    already happened — the row is written by the time this is called, so the
    alternative would be an exception thrown about a state change that is
    already a fact.
    """
    for callback in _OBSERVER:
        try:
            callback(payload)
        except Exception:
            _logger.warning(
                "runs observer raised; the run is unaffected",
                extra={"docdoc": {"event": "runs.observer_failed"}},
            )


#: The constants `reason` may take when it is not an error class name.
#:
#: A closed set, named here rather than spelled at each call site, because
#: `reason` is the one field a caller could put anything into and it travels to a
#: log line. Enumerating the constants makes "is this a class name or one of
#: these?" a question a test can ask.
#:
#: `"cancelled"` was missing and the omission was not cosmetic. A cancelled run
#: carries no `error_class`, because nothing refused anything, so the
#: `error_class or "completed"` both queues used reported every cancellation as
#: a completion. Deliberate stops were indistinguishable in the log from
#: successful ones — in the one place built to make a run's history legible.
REASONS = frozenset(
    {
        "submitted",  # the run came into existence
        "claimed",  # a worker took it
        "redelivered",  # a worker took it again after a lease lapsed
        "released",  # a worker gave it back before its lease expired
        "completed",  # finished with no error to name
        "cancelled",  # it actually stopped, as against the request below
        "cancel_requested",  # a caller asked; the run is still running
    }
)

_logger = logging.getLogger("docdoc.runs")


def reason_for(outcome: Any) -> str:
    """What the transition event calls one ending.

    Here rather than in either queue because both need it and they must not
    answer differently: a fake that labels a transition differently from the
    real implementation is a fake nothing can be tested against.

    A cancelled run has no ``error_class`` — nothing refused anything — so the
    ``error_class or "completed"`` both queues used labelled every cancellation
    a completion. An operator counting completions counted deliberate stops
    among them, in the one place built to make a run's history legible.
    """
    from docdoc.runs.model import RunStatus

    if outcome.status is RunStatus.CANCELLED:
        return "cancelled"
    return str(outcome.error_class or "completed")


def log_transition(
    *,
    run_id: UUID,
    tenant_id: str,
    from_state: str | None,
    to_state: str,
    attempts: int,
    worker_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Record one state change.

    **Called from the queue, not from the queue's callers.** That is the whole
    of what convergence corrected here: this function had one call site, in the
    worker's terminal path, so a claim, a lease expiry, an abandonment, and a
    cancellation each changed a run's state and said nothing. Emitting from the
    implementation means a transition cannot happen without an event, whereas
    emitting from the caller means every future caller has to remember — and the
    five silent transitions above are what forgetting looks like.

    `from_state` is ``None`` for the one event that is not a transition between
    states: a run coming into existence. Writing it as `None` rather than as
    `"absent"` keeps the payload honest — there was no previous state, as against
    a previous state named "absent".

    `reason` is a class name or a short constant like ``"completed"`` — never a
    message, for the reason `PipelineResult` already gives about `failure_class`:
    a message can quote the document it choked on.

    `tenant_id` is included because an operator debugging a stuck queue needs to
    know whose runs are stuck, and it is not secret to the deployment holding it.
    It is nonetheless absent from every HTTP response (`Run.dump_public`), where
    emitting it would give one tenant a value to compare against another's.
    """
    payload = {
        "event": EVENT_NAME,
        "run_id": str(run_id),
        "tenant_id": tenant_id,
        "from_state": from_state,
        "to_state": to_state,
        "attempts": attempts,
        "worker_id": worker_id,
        "reason": reason,
    }
    # **The log line is unchanged, byte for byte.** FR-024 forbids export from
    # altering an existing emission point, and an exporter that enriched this
    # payload "harmlessly" is how a deployment's log parsing breaks at the same
    # moment its tracing starts working. The bridge is handed the same mapping
    # this line serialises; it is not given a richer one.
    _logger.info(json.dumps(payload))
    _notify(payload)
