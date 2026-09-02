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
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

__all__ = ["EVENT_NAME", "log_transition"]

EVENT_NAME = "run.transition"

_logger = logging.getLogger("docdoc.runs")


def log_transition(
    *,
    run_id: UUID,
    tenant_id: str,
    from_state: str,
    to_state: str,
    attempts: int,
    worker_id: str | None = None,
    reason: str | None = None,
) -> None:
    """Record one state change.

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
