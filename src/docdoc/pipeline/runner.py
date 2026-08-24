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

import logging
import time
from typing import TYPE_CHECKING, Any

from docdoc.artifacts.errors import ArtifactError
from docdoc.extraction.extract import ExtractionResult
from docdoc.grounding.result import GroundingResult
from docdoc.kernel import Document
from docdoc.pipeline.errors import PipelineError
from docdoc.pipeline.plan import (
    extract_artifact_id,
    ground_artifact_id,
    validate_artifact_id,
)
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
    spec_for,
)
from docdoc.validation.result import ValidationResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    from docdoc.artifacts import ArtifactStore
    from docdoc.ingest.source import Limits, SourceFile

__all__ = ["run"]

_logger = logging.getLogger("docdoc.pipeline")


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


class _Reuse:
    """The store, wrapped in the two rules the pipeline adds to it.

    **Degradation** (FR-063). A store that cannot be read or written is not a
    failure of the run: the result is already computed and correct, and losing a
    cache entry is no reason to lose it. Both halves are logged once and the run
    proceeds as one with no store would have. ``FileArtifactStore`` already
    degrades on ``OSError``; this catches what it cannot — a store handed in by a
    caller, or an ``ArtifactError`` raised on a *write* rather than a read.

    **A corrupt artifact still raises.** Degradation covers "the store is not
    there", never "the store is lying". An ``ArtifactError`` on a read means a
    payload did not match its ``content_id``, and recomputing over it would hide
    a failing disk behind a slower run (FR-014). It travels up as the typed error
    it is.

    **``verify`` skips reads and keeps writes** (FR-064), which is what makes the
    conflicting-write check fire on results that would otherwise have been read
    back rather than recomputed.
    """

    def __init__(self, store: ArtifactStore, *, verify: bool) -> None:
        self._store = store
        self._verify = verify
        self._degraded = False

    def get(self, artifact_id: str | None, model: type[Any], version: int) -> Any | None:
        """A hit, or ``None`` for every kind of miss."""
        if artifact_id is None or self._verify or self._degraded:
            return None
        try:
            return self._store.get(
                artifact_id, model=model, artifact_format_version=version
            )
        except ArtifactError:
            raise
        except Exception as error:
            self._degrade("unreadable", error)
            return None

    def put(self, artifact_id: str | None, payload: Any, **fields: Any) -> None:
        """Store a result, or do not, and never fail the run for it."""
        if artifact_id is None or self._degraded:
            return
        try:
            self._store.put(artifact_id, payload, **fields)
        except ArtifactError:
            # A conflicting write is the one symptom available for a processor
            # whose output moved without its version moving (FR-062), and it is
            # exactly what `verify` exists to provoke. Never swallowed.
            raise
        except Exception as error:
            self._degrade("unwritable", error)

    def _degrade(self, reason: str, error: BaseException) -> None:
        """Log once, then stop trying. FR-063's "the condition is logged once"."""
        if self._degraded:
            return
        self._degraded = True
        _logger.warning(
            "artifact store unavailable, continuing without reuse",
            extra={
                "event": f"pipeline.store_{reason}",
                "error": type(error).__name__,
            },
        )


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
    verify: bool = False,
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
        verify: Execute every stage and still write, so that the store's
            conflicting-write check fires on results that would otherwise have
            been read back. Without it a processor whose output drifted without
            its version moving is only ever caught by a cache miss that happens
            not to occur (FR-064).

    Returns:
        One ``PipelineResult``, whether or not every stage succeeded.

    Raises:
        PipelineError: the run could not be sequenced at all. A *stage* failure
            is not raised — it is recorded on the result (FR-004).
    """
    from docdoc.artifacts import NullArtifactStore

    reuse = _Reuse(store or NullArtifactStore(), verify=verify)

    outcomes: list[StageOutcome] = []
    processors: dict[str, dict[str, str]] = {}

    parsed = document
    extraction = grounding = validation = None
    failed_stage: Stage | None = None

    try:
        if parsed is None:
            clock = _Clock()
            # Routing and parser selection happen here; the parser does not.
            # That gap is what makes a cached parse skip the billable half while
            # still computing the text-layer verdict on every run (FR-061).
            plan = _plan_parse(source, limits=limits, options=options)
            document_id = plan.document_id

            cached = reuse.get(document_id, Document, _format(Stage.PARSE))
            if cached is not None:
                parsed = cached
                outcomes.append(_reused(Stage.PARSE, document_id, clock))
            else:
                parsed = _execute_parse(plan)
                reuse.put(
                    document_id,
                    parsed,
                    stage=Stage.PARSE.value,
                    # The source blob, which is what makes the chain walk back
                    # to a document rather than stopping at the first parse
                    # (FR-022, FR-024).
                    input_artifact_id=plan.file.blob_id,
                    processor_id=plan.parser.id,
                    processor_version=plan.parser.version,
                    options_hash=plan.options_hash,
                    artifact_format_version=_format(Stage.PARSE),
                )
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
        # The id this stage *will* produce if the provider answers with the
        # model it was asked for. See `plan.py`: the extract options hash folds
        # the model that answered, so this is a prediction rather than a
        # derivation, and the write below only happens when it came true.
        expected = extract_artifact_id(
            parsed.id, schema=schema, registry=registry, adapter=adapter
        )
        cached = reuse.get(expected, ExtractionResult, _format(Stage.EXTRACT))
        if cached is not None:
            extraction = _retyped(cached, schema=schema, registry=registry)
            outcomes.append(_reused(Stage.EXTRACT, expected, clock))
        else:
            extraction = _extract(parsed, schema=schema, registry=registry, adapter=adapter)
            if extraction.artifact_id == expected:
                reuse.put(
                    expected,
                    extraction,
                    stage=Stage.EXTRACT.value,
                    input_artifact_id=parsed.id,
                    **_processor_record(Stage.EXTRACT, extraction),
                    artifact_format_version=_format(Stage.EXTRACT),
                )
            else:
                # The provider served a different model than the one requested —
                # an alias that has rolled, most likely. The result is correct
                # and is returned; it is simply not filed, because the only
                # identity it could be filed under is one a future run asking for
                # the same thing would never look up.
                _logger.info(
                    "extraction not cached: the model that answered is not the "
                    "one requested",
                    extra={
                        "event": "pipeline.extract_not_cacheable",
                        "expected_artifact_id": expected,
                        "actual_artifact_id": extraction.artifact_id,
                    },
                )
            outcomes.append(_executed(Stage.EXTRACT, extraction, clock))
        processors[Stage.EXTRACT.value] = _processor_record(Stage.EXTRACT, extraction)

        clock = _Clock()
        expected = ground_artifact_id(extraction.artifact_id)
        cached = reuse.get(expected, GroundingResult, _format(Stage.GROUND))
        if cached is not None:
            grounding = cached
            outcomes.append(_reused(Stage.GROUND, expected, clock))
        else:
            grounding = _ground(parsed, extraction)
            reuse.put(
                grounding.artifact_id,
                grounding,
                stage=Stage.GROUND.value,
                input_artifact_id=extraction.artifact_id,
                **_processor_record(Stage.GROUND, grounding),
                artifact_format_version=_format(Stage.GROUND),
            )
            outcomes.append(_executed(Stage.GROUND, grounding, clock))
        processors[Stage.GROUND.value] = _processor_record(Stage.GROUND, grounding)

        clock = _Clock()
        expected = validate_artifact_id(
            grounding.artifact_id, schema=schema, registry=registry
        )
        cached = reuse.get(expected, ValidationResult, _format(Stage.VALIDATE))
        if cached is not None:
            validation = cached
            outcomes.append(_reused(Stage.VALIDATE, expected, clock))
        else:
            validation = _validate(extraction, grounding, registry=registry, schema=schema)
            reuse.put(
                validation.artifact_id,
                validation,
                stage=Stage.VALIDATE.value,
                input_artifact_id=grounding.artifact_id,
                **_processor_record(Stage.VALIDATE, validation),
                artifact_format_version=_format(Stage.VALIDATE),
            )
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


def _reused(stage: Stage, artifact_id: str, clock: _Clock) -> StageOutcome:
    """A stage answered from the store.

    The duration is still recorded and is still real — it is the cost of the
    lookup, which is what makes the difference between a reused run and an
    executed one legible in the numbers as well as in the status.
    """
    return StageOutcome(
        stage=stage,
        status=StageStatus.REUSED,
        artifact_id=artifact_id,
        duration_ms=clock.elapsed_ms(),
    )


def _format(stage: Stage) -> int:
    """The artifact-format version of what this stage stores (ADR-0010 §3)."""
    return spec_for(stage).artifact_format_version


def _retyped(extraction: ExtractionResult, *, schema: str, registry: Any) -> ExtractionResult:
    """Restore the types a JSON round trip flattened out of a stored extraction.

    A stored ``Decimal`` comes back a string, because ``values`` is
    ``dict[str, Any]`` and only the schema knows which strings were numbers. Left
    alone, validation would compare a total against a sum of *strings* and reach
    a different verdict than the run that produced the artifact — so the reused
    result would not be indistinguishable from the executed one, which is exactly
    what FR-012 requires it to be.

    The coercion itself belongs to the extraction layer and stays there; this
    asks for it. A failure to retype is not a failure of the run: the stored
    result is returned as it was read, because a value that no longer fits its
    schema means the schema moved, and a schema that moved gives this stage a
    different identity anyway.
    """
    from docdoc.extraction.conform import retype

    try:
        entry = registry.resolve(schema)
        return extraction.model_copy(
            update={"values": retype(extraction.values, entry.schema)}
        )
    except Exception as error:
        _logger.info(
            "reused extraction could not be retyped against its schema",
            extra={
                "event": "pipeline.retype_skipped",
                "artifact_id": extraction.artifact_id,
                "error": type(error).__name__,
            },
        )
        return extraction


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


def _plan_parse(
    source: SourceFile | bytes,
    *,
    limits: Limits | None,
    options: Mapping[str, Any] | None,
) -> Any:
    from docdoc.ingest.parse import plan_parse

    return plan_parse(source, limits=limits, options=options)


def _execute_parse(plan: Any) -> Document:
    from docdoc.ingest.parse import execute_plan

    return execute_plan(plan)


def _extract(document: Document, *, schema: str, registry: Any, adapter: Any) -> Any:
    from docdoc.extraction.extract import extract

    return extract(document, schema=schema, registry=registry, adapter=adapter)


def _ground(document: Document, extraction: Any) -> Any:
    from docdoc.grounding import ground

    return ground(document, extraction)


def _validate(extraction: Any, grounding: Any, *, registry: Any, schema: str) -> Any:
    from docdoc.validation import validate

    return validate(extraction, grounding, registry.resolve(schema).schema)
