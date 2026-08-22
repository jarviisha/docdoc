"""parse → extract → ground → validate, in one place, for the first time.

Until this existed the sequence was written out inside one private function of
:mod:`docdoc.recording` — a package that exists to serve evaluation, so the order
in which docdoc processes a document lived in the module least likely to be read
by somebody asking what the order is. That module now calls this one.

**It sequences; it does not reimplement.** Every rule about what a stage means
stays in that stage's layer (FR-003). This decides what runs, in what order, what
is reused, and what is recorded — and nothing else.

**A failure ends the run and keeps everything before it** (FR-004), and is
attributed to the layer that *declared* the error rather than to whatever was
executing when it surfaced (FR-005). A grounding error raised during validation
is a grounding error, and attributing it to validation would send whoever reads
the report to the wrong code. That rule is lifted from
``docdoc/recording/record.py``, which needed it first.

**The module is ``runner``, not ``run``.** The public function is ``run``, and a
module of the same name in the same package is shadowed by the re-export in
``__init__`` -- so ``docdoc.pipeline.run`` resolves to the function, and anything
reaching for the module by that path silently gets something else. One name, one
thing.

**The pipeline adds no retries.** Provider and network retry policy already lives
in the layer that makes those calls, and validation, grounding, and schema errors
are never retried because there is no transient failure mode in a deterministic
computation (FR-010).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from docdoc.pipeline.errors import PipelineError
from docdoc.pipeline.result import (
    PipelineResult,
    RunProvenance,
    StageOutcome,
    StageStatus,
)
from docdoc.pipeline.stages import (
    PIPELINE_ID,
    PIPELINE_VERSION,
    Stage,
    artifact_id_of,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from docdoc.artifacts import ArtifactStore
    from docdoc.ingest.source import Limits, SourceFile
    from docdoc.kernel import Document

__all__ = ["run"]


def _stage_of(error: BaseException) -> Stage:
    """Which stage an exception came from, by its declaring layer.

    Read off the exception's own module rather than from where it was caught.
    The same function, for the same reason, as ``recording/record.py``'s — and
    when that module is rewritten to call this one, this becomes the only copy.
    """
    module = type(error).__module__
    if ".ingest" in module or ".kernel" in module:
        return Stage.PARSE
    if ".extraction" in module:
        return Stage.EXTRACT
    if ".grounding" in module:
        return Stage.GROUND
    return Stage.VALIDATE


class _Clock:
    """Wall time for one stage, in whole milliseconds.

    Monotonic, because a duration computed from a wall clock can go backwards
    across an NTP step and a negative cost is worse than a slightly imprecise
    one. The value enters no identity (FR-060).
    """

    def __init__(self) -> None:
        self._started = time.monotonic()

    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._started) * 1000)


def run(
    source: SourceFile | bytes,
    *,
    schema: str,
    registry: Any,
    adapter: Any,
    store: ArtifactStore | None = None,
    document: Document | None = None,
    limits: Limits | None = None,
    options: Mapping[str, Any] | None = None,
    request_id: str | None = None,
) -> PipelineResult:
    """Run the four stages over one document.

    Args:
        source: The bytes, or a ``SourceFile`` the caller already built.
        schema: The schema identity, as data. The pipeline branches on nothing.
        registry: A ``SchemaRegistry`` resolving ``schema``.
        adapter: The model adapter. Configuration picks it; this signature names
            no provider.
        store: Where to look for and record artifacts. Defaults to
            ``NullArtifactStore`` — there is no default location, because
            artifacts hold extracted values and blobs hold whole documents
            (FR-017, FR-044).
        document: An already-parsed document, which skips the parse. How a caller
            who already holds one avoids re-parsing it.
        limits: Size, page-count, and media-type limits, enforced before any
            parse or transmission. Reused from ingest rather than redefined
            (FR-039, FR-040).
        options: Parse options, folded into ``document_id``.
        request_id: Correlation only. Enters no identity (FR-060).

    Returns:
        One ``PipelineResult``, whether or not every stage succeeded.

    Raises:
        PipelineError: the run could not be sequenced at all. A *stage* failure
            is not raised — it is recorded on the result (FR-004).
    """
    from docdoc.artifacts import NullArtifactStore

    store = store or NullArtifactStore()

    outcomes: list[StageOutcome] = []
    processors: dict[str, dict[str, str]] = {}

    parsed = document
    extraction = grounding = validation = None
    failed_stage: Stage | None = None

    try:
        if parsed is None:
            clock = _Clock()
            parsed = _parse(source, limits=limits, options=options)
            outcomes.append(_executed(Stage.PARSE, parsed, clock))
        else:
            # Supplied rather than produced. Recorded as reused because that is
            # what it is from this run's point of view: work this run did not do.
            outcomes.append(
                StageOutcome(
                    stage=Stage.PARSE,
                    status=StageStatus.REUSED,
                    artifact_id=parsed.id,
                )
            )
        processors[Stage.PARSE.value] = {
            "processor_id": parsed.provenance.parser_id,
            "processor_version": parsed.provenance.parser_version,
            "options_hash": parsed.provenance.options_hash,
        }

        clock = _Clock()
        extraction = _extract(parsed, schema=schema, registry=registry, adapter=adapter)
        outcomes.append(_executed(Stage.EXTRACT, extraction, clock))
        processors[Stage.EXTRACT.value] = _processor_record(Stage.EXTRACT, extraction)

        clock = _Clock()
        grounding = _ground(parsed, extraction)
        outcomes.append(_executed(Stage.GROUND, grounding, clock))
        processors[Stage.GROUND.value] = _processor_record(Stage.GROUND, grounding)

        clock = _Clock()
        validation = _validate(extraction, grounding, registry=registry, schema=schema)
        outcomes.append(_executed(Stage.VALIDATE, validation, clock))
        processors[Stage.VALIDATE.value] = _processor_record(Stage.VALIDATE, validation)

    except PipelineError:
        # Sequencing itself failed. Not a stage's error, and not something to
        # record as one.
        raise
    except Exception as error:
        failed_stage = _stage_of(error)
        outcomes.append(
            StageOutcome(
                stage=failed_stage,
                status=StageStatus.FAILED,
                # The class name, never the message: a message can quote the
                # content it choked on, and this field reaches logs and error
                # bodies.
                failure_class=type(error).__name__,
            )
        )
        outcomes.extend(_skipped_after(failed_stage))

    return PipelineResult(
        outcomes=tuple(outcomes),
        provenance=RunProvenance(
            pipeline_id=PIPELINE_ID,
            pipeline_version=PIPELINE_VERSION,
            request_id=request_id,
            schema_identity=schema,
            schema_hash=None if extraction is None else extraction.provenance.schema_hash,
            processors=processors,
        ),
        document=parsed,
        extraction=extraction,
        grounding=grounding,
        validation=validation,
        processing_id=None if validation is None else validation.artifact_id,
        failed_stage=failed_stage,
    )


def _processor_record(stage: Stage, result: Any) -> dict[str, str]:
    """One stage's processor identity, version, and options hash.

    Every value here is asked of the layer that owns it — the options hash by
    calling that layer's own ``options_hash_for_*``, never by re-folding the
    inputs. Re-folding would put the folded set in two places, and the moment
    they disagreed the store would answer with an artifact for inputs that had
    changed (FR-058).
    """
    provenance = result.provenance

    if stage is Stage.EXTRACT:
        from docdoc.extraction.identity import EXTRACTOR_ID, options_hash_for_extraction

        decoding = provenance.decoding
        return {
            "processor_id": EXTRACTOR_ID,
            "processor_version": provenance.extractor_version,
            "options_hash": options_hash_for_extraction(
                schema_identity=provenance.schema_identity,
                schema_hash=provenance.schema_hash,
                prompt_hash=provenance.prompt_hash,
                projection_id=provenance.projection_id,
                model_id=provenance.model_id,
                model_version=provenance.model_version,
                max_output_tokens=decoding.max_output_tokens,
                temperature=decoding.temperature,
                top_p=decoding.top_p,
                top_k=decoding.top_k,
                seed=decoding.seed,
                thinking_budget=decoding.thinking_budget,
                input_budget_tokens=decoding.input_budget_tokens,
            ),
        }

    if stage is Stage.GROUND:
        from docdoc.grounding.identity import options_hash_for_grounding

        return {
            "processor_id": provenance.grounder_id,
            "processor_version": provenance.grounder_version,
            "options_hash": options_hash_for_grounding(provenance.options),
        }

    from docdoc.validation.identity import options_hash_for_validation

    return {
        "processor_id": provenance.validator_id,
        "processor_version": provenance.validator_version,
        "options_hash": options_hash_for_validation(
            provenance.options, enabled_rules=provenance.enabled_rules
        ),
    }


def _executed(stage: Stage, result: Any, clock: _Clock) -> StageOutcome:
    return StageOutcome(
        stage=stage,
        status=StageStatus.EXECUTED,
        artifact_id=artifact_id_of(stage, result),
        duration_ms=clock.elapsed_ms(),
    )


def _skipped_after(failed: Stage) -> list[StageOutcome]:
    """The stages that were never attempted, recorded rather than omitted.

    A missing outcome and a skipped one read the same in a result and mean
    different things — "we did not get there" versus "nobody asked".
    """
    order = list(Stage)
    return [
        StageOutcome(stage=stage, status=StageStatus.SKIPPED)
        for stage in order[order.index(failed) + 1 :]
    ]


# Stage calls are imported inside the functions that make them, not at module
# scope. `docdoc.ingest`'s package __init__ reaches its parser registry and,
# through it, PyMuPDF and the Azure SDK; a caller who supplies a parsed document
# should not need either installed to run the rest of the pipeline. The same
# reasoning `recording/record.py` already applies.


def _parse(
    source: SourceFile | bytes,
    *,
    limits: Limits | None,
    options: Mapping[str, Any] | None,
) -> Document:
    from docdoc.ingest import parse

    return parse(source, limits=limits, options=options)


def _extract(document: Document, *, schema: str, registry: Any, adapter: Any) -> Any:
    from docdoc.extraction.extract import extract

    return extract(document, schema=schema, registry=registry, adapter=adapter)


def _ground(document: Document, extraction: Any) -> Any:
    from docdoc.grounding import ground

    return ground(document, extraction)


def _validate(extraction: Any, grounding: Any, *, registry: Any, schema: str) -> Any:
    from docdoc.validation import validate

    return validate(extraction, grounding, registry.resolve(schema).schema)
