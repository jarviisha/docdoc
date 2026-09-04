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
    from uuid import UUID

__all__ = ["EVENT_NAME", "REASONS", "log_transition", "reason_for"]

EVENT_NAME = "run.transition"

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
    _logger.info(
        json.dumps(
            {
                "event": EVENT_NAME,
                "run_id": str(run_id),
                "tenant_id": tenant_id,
                "from_state": from_state,
                "to_state": to_state,
                "attempts": attempts,
                "worker_id": worker_id,
                "reason": reason,
            }
        )
    )
