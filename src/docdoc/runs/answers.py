"""A tenant's checks as an answer set, and a run as a prediction.

Both are pure: the caller supplies the stored facts (answers, a run, an artifact
store) and `docdoc.evaluation` does the scoring, unmodified.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from docdoc.evaluation import (
    DocumentOrigin,
    DocumentPrediction,
    GoldenDocument,
    GoldenSet,
    OriginKind,
    Stage,
    Tier,
)
from docdoc.evaluation.identity import golden_set_id_for
from docdoc.runs.model import RunStatus

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docdoc.runs.corrections import Answer
    from docdoc.runs.model import Run

__all__ = ["answer_set", "prediction_for"]

# The registry declares `line_items.amount`; a label names an entry, `line_items[1].amount`.
_INDEX = re.compile(r"\[\d*\]")


def answer_set(answers: Sequence[Answer], *, tenant_id: str, schema: Any) -> GoldenSet:
    """The tenant's answers as a golden set over `schema`'s declared fields."""
    declared = {path for path, *_ in schema.fields}
    by_blob: dict[str, list[Any]] = {}
    for item in answers:
        # An answer about a field the schema no longer declares is not scored.
        if _INDEX.sub("", item.correction.field_path) in declared:
            by_blob.setdefault(item.blob_id, []).append(item.correction.as_label())

    documents = tuple(
        GoldenDocument(
            document_id=blob_id,
            blob_sha256=blob_id,
            tier=Tier.RESTRICTED,
            origin=DocumentOrigin(
                kind=OriginKind.RESTRICTED,
                basis=f"submitted by tenant {tenant_id} for processing",
            ),
            schema_identity=schema.identity,
            schema_hash=schema.schema_hash,
            declared_label_count=len(labels),
        )
        for blob_id, labels in by_blob.items()
    )
    golden = GoldenSet(documents=documents, labels={b: tuple(ls) for b, ls in by_blob.items()})
    return golden.model_copy(update={"golden_set_id": golden_set_id_for(golden)})


def prediction_for(store: Any, run: Run, document_id: str) -> DocumentPrediction:
    """What `run` predicted, from the tenant's artifact store, or how it failed."""
    if run.status is RunStatus.SUCCEEDED:
        loaded = _load(store, run.processing_id)
        if loaded is not None:
            return DocumentPrediction(document_id=document_id, **loaded)
        return _failed(document_id, Stage.VALIDATE, "artifact_missing")

    try:
        stage = Stage(run.failed_stage) if run.failed_stage else Stage.PARSE
    except ValueError:
        stage = Stage.PARSE
    return _failed(document_id, stage, run.error_class or f"run_{run.status.value}")


def _failed(document_id: str, stage: Stage, reason: str) -> DocumentPrediction:
    return DocumentPrediction(document_id=document_id, failed_stage=stage, failure_reason=reason)


def _load(store: Any, processing_id: str | None) -> dict[str, Any] | None:
    """Walk validation -> grounding, extraction -> parse, as `_stored_result` does."""
    from docdoc.extraction.extract import ExtractionResult
    from docdoc.grounding.result import GroundingResult
    from docdoc.kernel.document import Document
    from docdoc.pipeline.stages import Stage as PipelineStage
    from docdoc.pipeline.stages import spec_for
    from docdoc.validation.result import ValidationResult

    if store is None or processing_id is None:
        return None

    def get(artifact_id: str, model: Any, stage: PipelineStage) -> Any:
        return store.get(
            artifact_id,
            model=model,
            artifact_format_version=spec_for(stage).artifact_format_version,
        )

    validation = get(processing_id, ValidationResult, PipelineStage.VALIDATE)
    if validation is None:
        return None
    provenance = validation.provenance
    grounding = get(provenance.grounding_artifact_id, GroundingResult, PipelineStage.GROUND)
    extraction = get(provenance.extraction_artifact_id, ExtractionResult, PipelineStage.EXTRACT)

    parsed = None
    if extraction is not None:
        parsed = get(extraction.provenance.document_id, Document, PipelineStage.PARSE)
    return {
        "extraction": extraction,
        "grounding": grounding,
        "validation": validation,
        "parser_id": None if parsed is None else parsed.provenance.parser_id,
        "parser_version": None if parsed is None else parsed.provenance.parser_version,
    }
