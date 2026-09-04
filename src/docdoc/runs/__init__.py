"""Asynchronous runs: the transport, and nothing that changes a result.

This layer exists because of one sentence in ADR-0010 §6, which chose a
synchronous job model and wrote down the objection a later reader would need:

    a job id that *is* the terminal artifact id cannot be issued before the run,
    because that id is not knowable until the stages feeding it have run.

That is still true, which is why this package does **not** add ``pending`` to
``GET /v1/jobs/{job_id}``. It introduces a second identity instead. A ``run_id``
is opaque and exists from the moment a request is accepted; a ``processing_id``
is content-addressed and exists once the run completes. Submitting the same
document twice yields two of the first and one of the second, and that is the
correct answer rather than a collision — a run is an *attempt*, a processing id
is a *result* (ADR-0013 §1).

**Nothing here can change what docdoc produces.** The worker calls
``pipeline.run()`` and copies six fields out of what comes back; no stage is
driven from this layer, no identity is derived here, and no result is
recomputed. SC-001 asserts that a result obtained through this layer and the
same result obtained synchronously agree on every value, verdict, location, and
identity — which is a property of that call being unchanged, not of two code
paths being kept in agreement.

**Where the clock lives.** ``identity.py`` is the only module in this package
permitted to read a clock or a random source. Everything else takes ``now`` and
``run_id`` as parameters. That is not ceremony: it is what keeps ``claim`` and
the state machine pure functions of ``(row, now)``, testable against an
in-memory fake at arbitrary times, and it is what stops a clock read drifting
down into ``pipeline`` where the determinism guard would — correctly — reject it
(FR-072, research R11).

**Layer position.** Above ``pipeline``, sibling of ``recording``, with an
independence contract between them. Neither uses the other: ``recording`` drives
the pipeline to produce a prediction set, this drives it to serve a request.

**What this package exports, and the one thing it does not.** Everything below
is I/O-free: the models, the protocol, and the errors. `PostgresRunQueue` is
deliberately absent and must be imported from `docdoc.runs.postgres`, because
re-exporting it would put `psycopg` in the import graph of anyone who typed
`import docdoc.runs` — and a base install has no `psycopg`, by design (SC-013).

The split is the interesting part. A caller who wants to *reason about* a run
needs `Run`, `RunStatus`, and the errors, and needs no driver to do it; a caller
who wants to *store* one is already choosing a backend and can say so in the
import. The empty `__all__` this file used to carry expressed the second half and
lost the first, which left this the only layer in the project exporting nothing —
`artifacts` exports ten names, `pipeline` twelve, `validation` twenty.
"""

from __future__ import annotations

from docdoc.runs.errors import (
    RunAbandonedError,
    RunError,
    RunNotCancellableError,
    RunNotFoundError,
    RunStateUnavailableError,
    TenantAssignmentError,
)
from docdoc.runs.model import (
    DEFAULT_TENANT,
    TERMINAL_STATES,
    Run,
    RunOutcome,
    RunStatus,
    StageOutcomeRecord,
)
from docdoc.runs.queue import RunQueue, RunSpec

__all__ = [
    "DEFAULT_TENANT",
    "TERMINAL_STATES",
    "Run",
    "RunAbandonedError",
    "RunError",
    "RunNotCancellableError",
    "RunNotFoundError",
    "RunOutcome",
    "RunQueue",
    "RunSpec",
    "RunStateUnavailableError",
    "RunStatus",
    "StageOutcomeRecord",
    "TenantAssignmentError",
]
