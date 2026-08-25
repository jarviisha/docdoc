"""``docdoc extract`` and ``docdoc inspect`` — one run, two renderings.

They are deliberately the same code path. ``extract`` reports *the result*;
``inspect`` reports *where every value came from*. Both are halves of the
Definition of Done stated at the project's founding — a PDF goes in one end of a
command, and a human can ask any extracted value which page and which rectangle
it came from — and splitting the run between them would have created two ways to
process a document that could drift apart.

**The exit code is decided here and only here.** ``0`` the document is valid,
``1`` the document is invalid, ``2`` the run could not complete. That split is
the point of the contract (FR-028): a script that treats "this invoice is wrong"
as "docdoc is broken" is the outcome a single non-zero code guarantees.

**A failed run still renders.** FR-004 keeps every result the stages before the
failure produced, and throwing them away at the last moment — after the parse has
been paid for and the model has answered — would defeat the requirement one layer
out from where it is honoured.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from docdoc.cli.config import empty_registry_message
from docdoc.cli.render import Rendering, field_rows, render_rows

if TYPE_CHECKING:
    import argparse

    from docdoc.cli.config import Settings
    from docdoc.pipeline import PipelineResult

__all__ = ["EXIT_COULD_NOT_RUN", "EXIT_INVALID", "EXIT_OK", "inspect", "run"]

#: The run completed and the document is valid.
EXIT_OK = 0
#: The run completed and the document is **invalid** — a real result, not an error.
EXIT_INVALID = 1
#: The run could not complete: a typed docdoc error.
EXIT_COULD_NOT_RUN = 2


def run(args: argparse.Namespace, settings: Settings) -> Rendering:
    """``docdoc extract`` — the result, with its verdict and its identities."""
    result = _pipeline_run(args, settings)
    data = _result_data(result)
    return Rendering(code=_exit_code(result), data=data, lines=_extract_lines(result, data))


def inspect(args: argparse.Namespace, settings: Settings) -> Rendering:
    """``docdoc inspect`` — every value, its verdict, its page, its rectangle.

    Two forms. Given a file, it runs the pipeline. Given ``--result ID``, it reads
    a stored result back out of the store — which is what FR-026's "inspect a
    result's values and their locations" asks for, and what the HTTP interface
    has always been able to do through ``GET /v1/jobs/{id}/result``.

    Somebody holding a ``processing_id`` from a log had an HTTP path and no
    command-line one; `checklists/interfaces.md` CHK024 raised the asymmetry and
    this closes it.
    """
    if getattr(args, "result", None):
        return _stored(args.result, settings)

    result = _pipeline_run(args, settings)
    data = _result_data(result)
    rows = data["fields"]
    lines = [*render_rows(rows), "", *_summary_lines(result, data)]
    return Rendering(code=_exit_code(result), data=data, lines=lines)


def _stored(processing_id: str, settings: Settings) -> Rendering:
    """Read a completed run back from the store by its terminal identity.

    Walks the chain the way the HTTP interface does: the validation artifact
    records its grounding input, which records its extraction input, so the whole
    result is reachable from the terminal id alone. That reachability is the one
    thing FR-022 asks this milestone to guarantee for a future collector, and it
    is what makes this command three lookups rather than a re-run.

    **Never recomputed.** A result that is not in the store is reported absent,
    for FR-036's reason: the inputs may have moved since, and producing a
    different result under the same identity would break the one promise that
    identity makes.
    """
    from docdoc.extraction.extract import ExtractionResult
    from docdoc.grounding.result import GroundingResult
    from docdoc.pipeline.stages import Stage, spec_for
    from docdoc.validation.result import ValidationResult

    if not settings.has_store:
        return _absent(
            processing_id,
            "no store is configured, so no result was ever recorded. "
            "Pass --store DIR or set DOCDOC_STORE_ROOT.",
            reason="no_store",
        )

    store = settings.store()

    def load(artifact_id: str | None, model: Any, stage: Stage) -> Any:
        if artifact_id is None:
            return None
        return store.get(
            artifact_id,
            model=model,
            artifact_format_version=spec_for(stage).artifact_format_version,
        )

    validation = load(processing_id, ValidationResult, Stage.VALIDATE)
    if validation is None:
        return _absent(
            processing_id,
            f"{processing_id} is not in this store. It was produced elsewhere, "
            "produced with no store, or cleared — and it is not recomputed, "
            "because the inputs may have moved since.",
            reason="not_in_store",
        )

    provenance = validation.provenance
    grounding = load(provenance.grounding_artifact_id, GroundingResult, Stage.GROUND)
    extraction = load(provenance.extraction_artifact_id, ExtractionResult, Stage.EXTRACT)

    rows = field_rows(extraction, grounding, validation)
    data: dict[str, Any] = {
        "processing_id": processing_id,
        "document_id": provenance.document_id,
        "schema": provenance.schema_identity,
        "verdict": validation.verdict.value,
        "fields": rows,
        # No outcomes: this is a retrieval, not a run. Reporting stage statuses
        # for work this invocation did not do would be fiction.
        "outcomes": [],
        "failed_stage": None,
        "counts": validation.counts.model_dump(mode="json"),
        "source": "store",
    }
    lines = [
        f"schema     {provenance.schema_identity}",
        f"document   {provenance.document_id}",
        f"verdict    {validation.verdict.value}",
        "",
        *render_rows(rows),
        "",
        f"processing {processing_id}",
        "           read from the store; no stage was executed",
    ]
    verdict_code = EXIT_OK if validation.verdict is _valid() else EXIT_INVALID
    return Rendering(code=verdict_code, data=data, lines=lines)


def _valid() -> Any:
    from docdoc.validation.result import Verdict

    return Verdict.VALID


def _absent(processing_id: str, message: str, *, reason: str) -> Rendering:
    """Say so plainly, and do not guess.

    Exit zero: being asked about an identity this store does not hold is not a
    failure of the command. It answered correctly.
    """
    from docdoc.cli.render import warn

    warn(message)
    return Rendering(
        code=EXIT_OK,
        data={"processing_id": processing_id, "result": None, "reason": reason},
        lines=[],
    )


def _pipeline_run(args: argparse.Namespace, settings: Settings) -> PipelineResult:
    """Call the pipeline. Nothing else in this module touches a stage.

    The empty-registry check happens *before* the run rather than as a rescue
    afterwards, so a fresh install pays nothing to be told it has no schemas
    (US1, scenario 5).
    """
    from docdoc.extraction.errors import SchemaError
    from docdoc.pipeline import run as run_pipeline

    registry = settings.registry()
    if args.schema not in registry:
        message = empty_registry_message(settings)
        if message is not None:
            raise SchemaError(
                message, identity=args.schema, available=registry.identities()
            )

    return run_pipeline(
        Path(args.file).read_bytes(),
        schema=args.schema,
        registry=registry,
        adapter=settings.adapter(),
        store=settings.store(),
        limits=settings.limits(),
        verify=settings.verify_cache,
    )


def _exit_code(result: PipelineResult) -> int:
    """Three outcomes, and the one that is not an error.

    A document that fails validation ran perfectly: the pipeline did what it was
    asked and the answer is that the document is wrong. That is ``1``. ``2`` is
    reserved for a run that could not produce an answer at all.
    """
    from docdoc.validation.result import Verdict

    if result.failed_stage is not None:
        return EXIT_COULD_NOT_RUN
    if result.validation is None:
        return EXIT_COULD_NOT_RUN
    return EXIT_OK if result.validation.verdict is Verdict.VALID else EXIT_INVALID


def _result_data(result: PipelineResult) -> dict[str, Any]:
    """The machine form: one document carrying the facts and the identities."""
    validation = result.validation
    return {
        "processing_id": result.processing_id,
        "document_id": None if result.document is None else result.document.id,
        "schema": result.provenance.schema_identity,
        "verdict": None if validation is None else validation.verdict.value,
        "fields": field_rows(result.extraction, result.grounding, validation),
        "outcomes": [
            {
                "stage": outcome.stage.value,
                "status": outcome.status.value,
                "artifact_id": outcome.artifact_id,
                "duration_ms": outcome.duration_ms,
                "failure_class": outcome.failure_class,
            }
            for outcome in result.outcomes
        ],
        "failed_stage": None if result.failed_stage is None else result.failed_stage.value,
        "cost": result.cost_summary(),
        "provenance": result.provenance.model_dump(mode="json"),
        "counts": None if validation is None else validation.counts.model_dump(mode="json"),
    }


def _extract_lines(result: PipelineResult, data: dict[str, Any]) -> list[str]:
    return [
        f"schema     {data['schema']}",
        f"document   {data['document_id']}",
        f"verdict    {data['verdict'] or '-'}",
        "",
        *render_rows(data["fields"]),
        "",
        *_summary_lines(result, data),
    ]


def _summary_lines(result: PipelineResult, data: dict[str, Any]) -> list[str]:
    """What the run cost and, if it failed, where — readable off the run (SC-004)."""
    cost = data["cost"]
    stages = "  ".join(f"{name}:{status}" for name, status in cost["stages"].items())
    lines = [
        f"stages     {stages}",
        f"cost       {cost['executed']} executed, {cost['reused']} reused, "
        f"{cost['duration_ms']} ms",
    ]
    if result.processing_id:
        lines.append(f"processing {result.processing_id}")
    if result.failed_stage is not None:
        outcome = result.outcome_for(result.failed_stage)
        failure = "-" if outcome is None else outcome.failure_class
        # The class name, never the message: a message can quote the document,
        # and this line reaches terminals, CI logs, and issue reports.
        lines.append(f"FAILED     at {result.failed_stage.value}: {failure}")
        # Only when there are any. A run that failed at the *first* stage has no
        # preceding results, and saying they are above when nothing is above
        # sends a reader looking for output that was never produced.
        if data["fields"] or result.extraction is not None:
            lines.append("           results from the preceding stages are above (FR-004)")
    return lines
