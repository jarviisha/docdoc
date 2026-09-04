"""FR-037 — `error_class` is a class name, and never a message.

The rule matters because of where this field goes: into a run row an operator
reads, into a `run.transition` log line, and into an HTTP error body — three
places a document's contents must not reach. An exception message is precisely
the string most likely to quote what it choked on. `ProviderError` is safe to put
anywhere; `"invalid value 'Acme Corp, 1420.00' at line 3"` is not.

**The interesting property is that this layer does not enforce the rule — it
inherits it.** `PipelineResult` has already reduced every failure to
`type(error).__name__`, and `RunOutcome.of` copies that field across. So the
assertion here is on the *projection*: that it copies rather than substitutes,
and that there is no path by which a message could be introduced.

That is a stronger thing to test than "the string looks like a class name",
because a string that looks like one is what a careless `str(exc)[:40]` also
produces.
"""

from __future__ import annotations

import inspect

from docdoc.pipeline.result import (
    PipelineResult,
    RunProvenance,
    StageOutcome,
    StageStatus,
)
from docdoc.pipeline.stages import Stage
from docdoc.runs.model import RunOutcome, RunStatus

#: A message of the kind a provider actually produces: it quotes the document.
LEAKY_MESSAGE = "invalid value 'Acme Corporation — 1420.00 EUR' at line 3"


def _failed_result(*, failure_class: str) -> PipelineResult:
    return PipelineResult(
        outcomes=(
            StageOutcome(stage=Stage.PARSE, status=StageStatus.EXECUTED, artifact_id="sha256:aa"),
            StageOutcome(
                stage=Stage.EXTRACT, status=StageStatus.FAILED, failure_class=failure_class
            ),
            StageOutcome(stage=Stage.GROUND, status=StageStatus.SKIPPED),
            StageOutcome(stage=Stage.VALIDATE, status=StageStatus.SKIPPED),
        ),
        provenance=RunProvenance(
            pipeline_id="docdoc-pipeline", pipeline_version="1.0.0", schema_identity="invoice@1"
        ),
        failed_stage=Stage.EXTRACT,
    )


def test_the_projection_copies_the_class_name_it_was_given() -> None:
    """The whole of the mechanism: read one field, write one field."""
    outcome = RunOutcome.of(_failed_result(failure_class="ProviderError"))

    assert outcome.status is RunStatus.FAILED
    assert outcome.error_class == "ProviderError"
    assert outcome.failed_stage == "extract"


def test_the_projection_substitutes_nothing_of_its_own() -> None:
    """The assertion FR-037 actually needs.

    Given a `failure_class` that is (wrongly) a message, the projection must
    still copy it verbatim rather than reformat, truncate, or enrich it. That
    sounds backwards until you see what it rules out: any transformation here
    would be a second place where the no-content rule is decided, and the second
    place is the one that eventually decides differently.

    The rule is enforced where the value is *created* — `runner.py` records
    `type(error).__name__` — and this pins that this layer does not undo it.
    """
    outcome = RunOutcome.of(_failed_result(failure_class=LEAKY_MESSAGE))

    assert outcome.error_class == LEAKY_MESSAGE, (
        "the projection rewrote the value it was given. Whatever it produces "
        "instead is now a second definition of what an error_class is, and the "
        "one in `runner.py` is no longer the only one"
    )


def test_the_pipeline_records_a_class_name_and_not_a_message() -> None:
    """Where the rule is actually enforced, asserted at its source.

    Read off the source rather than provoked with a failing stage, because the
    claim is about *every* failure — including the ones no fixture produces — and
    a single provoked exception would prove it for one.
    """
    from docdoc.pipeline import runner

    source = inspect.getsource(runner)

    assert "failure_class=type(error).__name__" in source, (
        "the pipeline no longer records the error's class name. Whatever it "
        "records instead now travels into run rows, log lines, and HTTP error "
        "bodies, and FR-037's argument has to be made again for it"
    )
    assert "failure_class=str(error)" not in source


def test_a_successful_run_carries_no_error_class() -> None:
    """The invariant's other side: nothing to name means no name."""
    result = PipelineResult(
        outcomes=(
            StageOutcome(stage=stage, status=StageStatus.EXECUTED, artifact_id="sha256:aa")
            for stage in Stage
        ),
        provenance=RunProvenance(
            pipeline_id="docdoc-pipeline", pipeline_version="1.0.0", schema_identity="invoice@1"
        ),
        processing_id="sha256:" + "e" * 64,
    )

    outcome = RunOutcome.of(result)

    assert outcome.status is RunStatus.SUCCEEDED
    assert outcome.error_class is None
    assert outcome.failed_stage is None


def test_a_cancelled_run_names_no_error_either() -> None:
    """Cancellation is not a failure, so there is no class to name.

    A cancelled run has no `failed_stage` and no failing outcome, so the
    projection has nothing to copy — which is the correct answer and not a gap.
    """
    result = PipelineResult(
        outcomes=(
            StageOutcome(stage=Stage.PARSE, status=StageStatus.EXECUTED, artifact_id="sha256:aa"),
            StageOutcome(stage=Stage.EXTRACT, status=StageStatus.SKIPPED),
            StageOutcome(stage=Stage.GROUND, status=StageStatus.SKIPPED),
            StageOutcome(stage=Stage.VALIDATE, status=StageStatus.SKIPPED),
        ),
        provenance=RunProvenance(
            pipeline_id="docdoc-pipeline", pipeline_version="1.0.0", schema_identity="invoice@1"
        ),
    )

    outcome = RunOutcome.of(result)

    assert outcome.status is RunStatus.CANCELLED
    assert outcome.error_class is None
    assert outcome.failed_stage is None
    assert outcome.processing_id is None


def test_the_stage_outcome_records_carry_class_names_too() -> None:
    """The per-stage copy follows the same rule, by the same mechanism.

    `stage_outcomes` reaches the same run row and the same HTTP response, so a
    rule that held for the top-level field and not for these would be a rule with
    a hole in exactly the shape of a partial failure.
    """
    outcome = RunOutcome.of(_failed_result(failure_class="ExtractionError"))

    failing = [record for record in outcome.stage_outcomes if record.failure_class]
    assert [record.failure_class for record in failing] == ["ExtractionError"]
    assert all(record.stage in {s.value for s in Stage} for record in outcome.stage_outcomes)
