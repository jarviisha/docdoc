"""What one run produced, and what it cost.

**A failed run keeps everything the stages before it produced.** That is the one
rule this module exists to hold, and it is inherited rather than invented:
``docdoc/recording/record.py`` already worked this way, because discarding a
partial result loses the evidence of what went wrong at exactly the moment
somebody needs it. A document that fails at ``VALIDATE`` is returned with its
extraction and its grounding.

**``failure_class`` is the error's class name, never its message.** An exception
message can quote the content it choked on, and this field travels into reports,
logs, and HTTP error bodies where FR-043 forbids that. The same rule, for the
same reason, as the recorder's.

**Durations are recorded and enter nothing.** No identity, no artifact, no
verdict (FR-060). They are here to answer what a run cost, and a cost that could
move a cache key would make every run's identity depend on how busy the machine
was.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

# Runtime imports, not TYPE_CHECKING ones: pydantic resolves field annotations
# when it builds the model. Each is a SUBMODULE import rather than a package one,
# which is the rule every layer above extraction follows -- importing
# `docdoc.extraction` executes its adapter registry and pulls a provider SDK into
# the graph, and the forbidden-imports contracts would break immediately.
from docdoc.extraction.extract import ExtractionResult
from docdoc.grounding.result import GroundingResult
from docdoc.kernel import Document
from docdoc.pipeline.stages import Stage
from docdoc.validation.result import ValidationResult

__all__ = [
    "PipelineResult",
    "RunProvenance",
    "StageOutcome",
    "StageStatus",
]


class StageStatus(StrEnum):
    """What happened to one stage."""

    EXECUTED = "executed"
    #: Returned from the store instead of being run. The result is required to be
    #: indistinguishable from the executed one.
    REUSED = "reused"
    #: An earlier stage failed, so this one was never attempted.
    SKIPPED = "skipped"
    FAILED = "failed"


class StageOutcome(BaseModel):
    """One stage's fate, with the cost of it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: Stage
    status: StageStatus

    #: ``None`` when the stage failed or was skipped.
    artifact_id: str | None = None

    #: Wall time. Never enters an identity (FR-060).
    duration_ms: int = Field(default=0, ge=0)

    #: The typed error's **class name**. Never its message.
    failure_class: str | None = None

    @property
    def ran(self) -> bool:
        """Whether this stage actually did the work, as opposed to reusing it."""
        return self.status is StageStatus.EXECUTED


class RunProvenance(BaseModel):
    """Everything needed to recompute the run's terminal identity.

    SC-006 is stated against exactly this model plus the per-stage outcomes: a
    recomputation that needs a field the run did not record is a failure of the
    criterion, not a gap in the test.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    pipeline_id: str
    pipeline_version: str

    #: Correlation only. Never enters an identity (FR-060), which is why it sits
    #: here beside the versions rather than inside any options hash.
    request_id: str | None = None

    schema_identity: str
    schema_hash: str | None = None

    #: Per stage: its processor id, its version, and its options hash, as the
    #: stage itself recorded them.
    processors: dict[str, dict[str, str]] = Field(default_factory=dict)


class PipelineResult(BaseModel):
    """One document, four stages, one result."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    outcomes: tuple[StageOutcome, ...]
    provenance: RunProvenance

    #: Carried for in-process callers — the recorder needs it, and so does
    #: anything that wants to slice text out of a located span — and **excluded
    #: from serialisation**.
    #:
    #: `research.md` left this open, to be decided by whichever consumer told us
    #: something. The consumer that did was `model_dump_json`: a `Document`
    #: contains a `SpanIndex`, which pydantic cannot serialise, so a result
    #: carrying one cannot be written to JSON at all. That would have made the
    #: CLI's `--json` and the whole HTTP layer impossible.
    #:
    #: Excluding it is not a loss. FR-034 requires a serialised result to carry
    #: the same *values, verdicts, locations, and identities*, and every one of
    #: those is on the extraction, grounding, and validation results. The
    #: document is reachable by `document_id` for anyone who needs the text.
    document: Document | None = Field(default=None, exclude=True)
    extraction: ExtractionResult | None = None
    grounding: GroundingResult | None = None
    validation: ValidationResult | None = None

    #: The terminal artifact id, which is the run's ``processing_id`` (ADR-0003,
    #: FR-007). Present exactly when ``validation`` is.
    processing_id: str | None = None

    failed_stage: Stage | None = None

    @property
    def succeeded(self) -> bool:
        return self.failed_stage is None and self.processing_id is not None

    @property
    def executed_count(self) -> int:
        """Stages that did the work. The numerator of every reuse claim."""
        return sum(1 for outcome in self.outcomes if outcome.status is StageStatus.EXECUTED)

    @property
    def reused_count(self) -> int:
        """Stages answered from the store."""
        return sum(1 for outcome in self.outcomes if outcome.status is StageStatus.REUSED)

    def failure_of(self, stage: Stage) -> str | None:
        """The class name of the error that stopped one stage, if it did."""
        outcome = self.outcome_for(stage)
        return None if outcome is None else outcome.failure_class

    def outcome_for(self, stage: Stage) -> StageOutcome | None:
        for outcome in self.outcomes:
            if outcome.stage is stage:
                return outcome
        return None

    def cost_summary(self) -> dict[str, Any]:
        """What this run cost, readable off the run itself (SC-004).

        Deliberately a plain mapping rather than a model: it is two counts and a
        duration, and a schema for that would be a schema for nothing.
        """
        return {
            "executed": self.executed_count,
            "reused": self.reused_count,
            "duration_ms": sum(outcome.duration_ms for outcome in self.outcomes),
            "stages": {outcome.stage.value: outcome.status.value for outcome in self.outcomes},
        }
