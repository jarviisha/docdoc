"""One event per stage, correlation across the four, and counters.

**This adds correlation, not a second logging system.** The four layers below
already emit their own structured events, and each already argues its own
no-content rule — ``validation/observe.py`` argues it for verdicts,
``grounding/observe.py`` for the match view. What none of them can supply is the
thing that ties four events to one run: a ``request_id`` the caller chose and a
``processing_id`` that does not exist until the last stage finishes.

So this emits one ``pipeline.stage`` event per stage carrying the correlation and
the cost, and leaves every layer's own event exactly as it was (research R9).

**Nothing here can change a result.** No identity reads a duration, a request id,
a retry count, or a counter (FR-049, FR-060). The observer is a callable a
deployment supplies; docdoc calls it and ignores what it returns, so a tracing
bridge that raises cannot fail a run that had already succeeded.

**What never appears:** document text, extracted values, claim text, prompt
bodies, credentials, or a provider's error message. Identifiers, hashes,
versions, counts, and timings only (FR-043). A stage failure is recorded by its
error's **class name**, which is the same rule ``PipelineResult`` follows and for
the same reason: a message can quote the document it choked on.

**There is no run-level event**, and that is a decision rather than an omission.
The four stage events share a ``request_id`` and the last carries the
``processing_id``, so a run is reconstructable by filtering on either. A fifth
event summarising the four would be a second place where the cost of a run is
stated, and the two would eventually disagree.
"""

from __future__ import annotations

import contextvars
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable

    from docdoc.pipeline.result import StageOutcome

__all__ = [
    "EVENT_NAME",
    "correlation",
    "counters",
    "log_stage",
    "observer",
    "request_id",
    "reset_counters",
    "set_observer",
]

EVENT_NAME = "pipeline.stage"

_logger = logging.getLogger("docdoc.pipeline")

#: The correlation identity for the run on this call stack. A context variable
#: rather than a parameter threaded through four call sites, because the layers
#: below neither have it nor should grow a parameter for it — and a
#: ``contextvar`` is the one mechanism that survives both a nested call and a
#: concurrently served request without either seeing the other's value.
_REQUEST_ID: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "docdoc_request_id", default=None
)

#: Stages executed and stages reused, since the last reset. Two integers, which
#: is what FR-047 asks for; a metrics library would be infrastructure the MVP
#: does not have a requirement for.
_COUNTS: dict[str, int] = {"executed": 0, "reused": 0, "failed": 0, "skipped": 0}

#: A deployment's bridge to whatever it uses — OpenTelemetry, most likely. A
#: callable and not a dependency (FR-048): a run with none configured behaves
#: identically, which is the whole requirement.
_OBSERVER: list[Callable[[dict[str, Any]], None]] = []


def set_observer(callback: Callable[[dict[str, Any]], None] | None) -> None:
    """Install, or remove, the span bridge.

    One observer, not a list of them. A deployment that wants two can write a
    function that calls two, and a registry of subscribers would be an event bus
    — infrastructure with no present-tense reason to exist (Principle XI).
    """
    _OBSERVER.clear()
    if callback is not None:
        _OBSERVER.append(callback)


def observer() -> Callable[[dict[str, Any]], None] | None:
    """The installed bridge, if any."""
    return _OBSERVER[0] if _OBSERVER else None


class correlation:  # noqa: N801 — used as a context manager, reads as one
    """Bind a request identity for the duration of a run.

    ``with correlation(request_id="abc"):`` — every stage event emitted inside
    carries it, and nothing outside does. The value is restored on exit even if
    the run raised, which is what makes a failed request stop tainting the next
    one on a reused thread.
    """

    __slots__ = ("_request_id", "_token")

    def __init__(self, *, request_id: str | None) -> None:
        self._request_id = request_id
        self._token: contextvars.Token[str | None] | None = None

    def __enter__(self) -> str | None:
        self._token = _REQUEST_ID.set(self._request_id)
        return self._request_id

    def __exit__(self, *_: object) -> None:
        if self._token is not None:
            _REQUEST_ID.reset(self._token)
            self._token = None


def request_id() -> str | None:
    """The correlation identity in scope, or ``None``."""
    return _REQUEST_ID.get()


def log_stage(
    outcome: StageOutcome,
    *,
    processing_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    usage: Any = None,
) -> None:
    """Emit the one event FR-045 requires, and count the stage.

    Every constitutionally required field is here: the request identity, the
    processing identity, the step identity, the duration, and the outcome —
    plus, where a provider answered, the provider, the model, and the token
    usage. The provider fields are *passed in* by the pipeline from the result
    the stage produced, never re-derived, because the layer that made the call
    is the one that knows what answered (research R9).

    ``processing_id`` is ``None`` for every stage except the last and for every
    failed run, because the terminal artifact is what it is and it does not exist
    until validation finishes. Recorded as absent rather than as a placeholder: a
    reader can tell "not yet" from "not applicable", and a synthesised value
    would be a second run identifier, which FR-007 forbids.
    """
    _COUNTS[outcome.status.value] = _COUNTS.get(outcome.status.value, 0) + 1

    payload: dict[str, Any] = {
        "event": EVENT_NAME,
        "request_id": _REQUEST_ID.get(),
        "processing_id": processing_id,
        "step_id": outcome.stage.value,
        "artifact_id": outcome.artifact_id,
        "outcome": outcome.status.value,
        # The named field FR-046 asks for, beside the status that already says
        # it. A cost question is answered by filtering on one boolean, which is
        # what makes it answerable from logs alone.
        "reused": outcome.status.value == "reused",
        "duration_ms": outcome.duration_ms,
    }
    if outcome.failure_class is not None:
        # The class, never the message (FR-043).
        payload["failure_class"] = outcome.failure_class
    if provider is not None:
        payload["provider"] = provider
    if model is not None:
        payload["model"] = model
    if usage is not None:
        payload["usage"] = _usage(usage)

    _logger.info(EVENT_NAME, extra={"docdoc": payload})
    _notify(payload)


def _usage(usage: Any) -> dict[str, Any]:
    """Token counts, and only counts.

    Read field by field rather than dumped wholesale, so that a future field on
    ``ModelUsage`` cannot arrive in a log without somebody deciding it should.
    """
    return {
        name: getattr(usage, name, None)
        for name in (
            "input_tokens",
            "output_tokens",
            "reasoning_tokens",
            "cache_read_input_tokens",
        )
        if getattr(usage, name, None) is not None
    }


def _notify(payload: dict[str, Any]) -> None:
    """Hand the event to the deployment's bridge, and survive it.

    A tracing exporter that raises must not fail a run whose stages all
    succeeded. The observer is an outer concern by construction (FR-049), and
    treating its failure as the run's would make observability able to change an
    outcome — the one thing it must never do.
    """
    for callback in _OBSERVER:
        try:
            callback(payload)
        except Exception:
            _logger.warning(
                "pipeline observer raised; the run is unaffected",
                extra={"docdoc": {"event": "pipeline.observer_failed"}},
            )


def counters() -> dict[str, int]:
    """Stages executed versus reused, and the two that are neither (FR-047)."""
    return dict(_COUNTS)


def reset_counters() -> None:
    """Zero the counters. For a test, or for a process that reports per batch."""
    for name in _COUNTS:
        _COUNTS[name] = 0
