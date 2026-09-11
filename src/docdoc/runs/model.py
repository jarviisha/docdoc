"""The run: an attempt, which is not a result.

A `Run` records one attempt to execute the pipeline over one document against one
schema. The **result** that attempt produces is content-addressed, immutable, and
lives in the artifact store under `processing_id`; this row points at it and
never duplicates it (ADR-0013 §1, §2).

That separation is why `RunOutcome.of()` below is a projection rather than a
translation. `PipelineResult` already carries every fact about what happened —
including, usefully, `failure_class`, which the pipeline has *already* reduced to
a class name. Copying it inherits FR-037's no-content rule instead of
re-enforcing it, and there is no second place where a run's outcome is stated to
drift from the first.

Nothing here performs I/O or reads a clock. Every timestamp arrives as an
argument from `docdoc.runs.identity`.
"""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from typing import TYPE_CHECKING, Any, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from docdoc.pipeline.result import PipelineResult

__all__ = [
    "DEFAULT_TENANT",
    "TERMINAL_STATES",
    "Priority",
    "Run",
    "RunOutcome",
    "RunStatus",
    "StageOutcomeRecord",
    "Tombstone",
]

#: The tenant a deployment has when authentication is off (FR-088).
#:
#: Its namespace in the store is the **root**, not `t/default/` — see
#: `docdoc.artifacts.paths.tenant_root`. Naming it here rather than leaving it
#: implicit means "one implicit tenant" is a value the code holds, not a
#: convention three modules assume separately.
DEFAULT_TENANT = "default"


class RunStatus(StrEnum):
    """Five states, and the set is closed (FR-006).

    There is deliberately **no** `expired`. An earlier draft of the data model
    listed one and described a retention sweep to set it, and no task ever built
    that sweep — a state no code path could reach is a state that lies to
    everyone who reads the enum. Retention is Milestone 10's, and `expires_at` is
    recorded meanwhile so that work inherits a deadline rather than inventing one.
    """

    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


#: No transition leaves these (data-model.md rule 7).
TERMINAL_STATES = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED})


# ---------------------------------------------------------------------------
# `RunStatus` ABOVE IS CLOSED AT FIVE AND MILESTONE 10 ADDS NOTHING TO IT.
#
# This is the file where a sixth member would be added by somebody who had not
# read FR-004a, and the reasoning is short enough to keep here rather than in a
# specification they would also not have read.
#
# Milestone 10 builds the retention sweep whose absence is the reason `expired`
# was left out. So the state is now reachable — and putting it back would swap
# one lie for another, because a `Run` carrying that status carries none of a
# run's other fields. A removed run is gone AS A RUN: what remains is a
# `Tombstone` below, four fields, in its own table. `tests/unit/
# test_run_status_set_is_closed.py` asserts the five by name (SC-023).
# ---------------------------------------------------------------------------


class Priority(IntEnum):
    """How eagerly a run is claimed. Two members, and the values are the order.

    An `IntEnum` because the claim query sorts by this column directly, and a
    gap between the two values so a third class — if one is ever justified —
    is a number rather than a migration of a type (data-model.md).

    **The client sets it, bounded by a per-tenant ceiling the operator
    configures** (FR-087a). Letting a client choose freely was rejected on the
    obvious ground: every client chooses the top of the scale, and in a shared
    deployment that is self-service escalation past another customer's queue.
    A request above the ceiling is accepted AT the ceiling and told so, never
    refused — an operator lowering a ceiling must not break a client that
    changed nothing.
    """

    ORDINARY = 0
    URGENT = 10

    @property
    def label(self) -> str:
        """What a caller sees: ``ordinary`` or ``urgent``, never ``0`` or ``10``.

        **The numbers are a storage and ordering detail** and they escaped into
        the HTTP surface once. A submission asks for ``"urgent"`` by name, so a
        response answering ``10`` makes the client learn a mapping to read back
        what it just sent — and it cannot check that mapping against anything,
        because the ceiling is deliberately readable through no route (FR-087b).
        A number would also make a third class a breaking change for every
        client that had hard-coded the two.
        """
        return self.name.lower()

    @classmethod
    def from_label(cls, label: str) -> Priority:
        """The member a caller named, or `KeyError`. The inverse of `label`."""
        return cls[label.strip().upper()]


class Tombstone(BaseModel):
    """What remains of a removed run. Four fields, and the shortness is the point.

    No status, no blob id, no schema identity, no stage outcomes (FR-004). A
    tombstone that carried what the run held would be a way of retaining what was
    deleted.

    `extra="forbid"` so a fifth field is a failure rather than a drift.

    Read by the owning tenant, which is told its run was here and is gone, with
    when and under which rule. Every other tenant is told what it would be told
    about an identifier that never existed — the two responses differ in kind,
    not in a field of one shape (FR-011).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    tenant_id: str
    deleted_at: datetime

    #: Which rule removed it: `retention` for the sweep, `erasure:tenant` or
    #: `erasure:document` for an operator's request. This field is why
    #: `RunStatus` gains no member — the distinction between ageing out and being
    #: erased has to be recorded somewhere, and a status carrying it too would be
    #: a second source of truth about one fact.
    policy: str


class StageOutcomeRecord(BaseModel):
    """What survives of a `StageOutcome` once the no-content rule is applied.

    Four fields, and the omissions are the point: no value, no claimed text, no
    message. `failure_class` arrives already reduced to a class name by the
    pipeline (FR-037).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: str
    status: str
    artifact_id: str | None = None
    duration_ms: int = Field(default=0, ge=0)
    failure_class: str | None = None


class RunOutcome(BaseModel):
    """What a worker writes back when a run stops.

    Constructed by `of()` from a `PipelineResult`, or directly for the failures
    that never reach the pipeline at all — an unresolvable schema (FR-091) and
    abandonment (FR-021).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    status: RunStatus
    processing_id: str | None = None
    failed_stage: str | None = None
    error_class: str | None = None
    stage_outcomes: tuple[StageOutcomeRecord, ...] = ()

    @model_validator(mode="after")
    def _identity_belongs_only_to_success(self) -> Self:
        """The one invariant a careless update could break (data-model.md).

        `processing_id` present exactly when the run succeeded. Enforced here as
        well as by a database check constraint, because the two identities of
        ADR-0013 §1 are only distinguishable while nothing pretends a failed run
        has a result.
        """
        succeeded = self.status is RunStatus.SUCCEEDED
        if succeeded and self.processing_id is None:
            raise ValueError("a succeeded run must carry its processing_id")
        if not succeeded and self.processing_id is not None:
            raise ValueError(f"a {self.status} run cannot carry a processing_id")
        return self

    @classmethod
    def of(cls, result: PipelineResult) -> RunOutcome:
        """Project a `PipelineResult` onto what the run row keeps (R2, FR-036).

        Six fields copied. No translation, no interpretation, and **no branch on
        schema or document type** — the worker treats `schema_identity` as an
        opaque string throughout, which is what keeps Principle VI's "no
        document-type-specific code path" true of this layer as well.

        A failed run keeps the outcomes of the stages that *did* complete. That
        is FR-036 and it is the whole of User Story 2: asynchronously, the caller
        is not holding the response that used to be the only place a failure
        existed.
        """
        outcomes = tuple(
            StageOutcomeRecord(
                stage=str(outcome.stage),
                status=str(outcome.status),
                artifact_id=outcome.artifact_id,
                duration_ms=outcome.duration_ms,
                failure_class=outcome.failure_class,
            )
            for outcome in result.outcomes
        )

        if result.failed_stage is None and result.processing_id is not None:
            return cls(
                status=RunStatus.SUCCEEDED,
                processing_id=result.processing_id,
                stage_outcomes=outcomes,
            )

        # A run can also stop with no `failed_stage` and no `processing_id`: that
        # is what cancellation looks like from here, because stopping between
        # stages produces no terminal artifact and raises nothing (FR-033).
        failing = next(
            (o for o in outcomes if o.failure_class is not None),
            None,
        )
        return cls(
            status=RunStatus.FAILED if result.failed_stage else RunStatus.CANCELLED,
            failed_stage=str(result.failed_stage) if result.failed_stage else None,
            error_class=failing.failure_class if failing else None,
            stage_outcomes=outcomes,
        )


class Run(BaseModel):
    """One attempt, as it is stored and as it is served.

    Mutable in the database and frozen here: a `Run` is a *reading* of the row,
    and a transition produces a new one rather than editing the old. That keeps
    every state change visible at a call site instead of happening through an
    attribute assignment somewhere.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: UUID
    tenant_id: str
    blob_id: str
    schema_identity: str
    status: RunStatus

    attempts: int = Field(default=0, ge=0)
    worker_id: str | None = None
    lease_until: datetime | None = None

    processing_id: str | None = None
    failed_stage: str | None = None
    error_class: str | None = None
    stage_outcomes: tuple[StageOutcomeRecord, ...] = ()

    request_id: str | None = None
    idempotency_key: str | None = None

    #: Milestone 10. Each defaults to what an existing row already means, so the
    #: migration that adds the columns changes no run's behaviour (FR-101).
    priority: Priority = Priority.ORDINARY
    #: The run's total, recorded by the worker in the same statement as the
    #: terminal state. `None` rather than `0` for a run that failed before
    #: reaching a provider: "used none" and "never got there" are different facts.
    tokens_used: int | None = None
    #: The destination registered at submission, or none.
    callback_id: UUID | None = None

    created_at: datetime
    updated_at: datetime
    expires_at: datetime

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATES

    def lease_expired_at(self, instant: datetime) -> bool:
        """Whether a running run's lease has lapsed by `instant`.

        Takes the instant rather than reading a clock, which is FR-072 and also
        what lets the claim policy be tested at any time without sleeping.
        """
        if self.status is not RunStatus.RUNNING or self.lease_until is None:
            return False
        return self.lease_until < instant

    def dump_public(self) -> dict[str, Any]:
        """The fields an API response may carry.

        `tenant_id` is excluded. It is not secret to its owner, and returning it
        would put a value in a body that a caller could compare against another
        tenant's — SC-008 requires cross-tenant responses to be byte-identical to
        non-existence, and the cheapest way to keep that true is to never emit
        the field that distinguishes them.

        Milestone 10 adds three columns and emits **one** of them.

        `priority` is emitted always, including when none was requested, because
        a submission above its tenant's ceiling is accepted *at* the ceiling and
        the caller has no other way to learn what it was granted (FR-087a).

        `tokens_used` is not. It is an enforcement counter, and a token total in
        a response body is the first half of an invoice — which constitution
        v1.8.0 keeps deferred in the same sentence that permits the counting.

        `callback_id` is not. The caller supplied it; what they cannot see
        without asking is what became of the delivery, and that is a route of its
        own. Echoing an input widens this contract for nothing.
        """
        body = self.model_dump(
            mode="json",
            exclude={"tenant_id", "idempotency_key", "tokens_used", "callback_id"},
        )
        # **The name, not the number.** `mode="json"` serialises an `IntEnum` as
        # its value, so this answered `10` to a caller that had asked for
        # `"urgent"` — contracts/operations-http-api.md says the name in three
        # places and the request side has always used one. See `Priority.label`.
        body["priority"] = self.priority.label
        return body
