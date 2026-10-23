"""A tenant's checks as an answer set, and a run as a prediction.

Both are pure: the caller supplies the stored facts (answers, a run, an artifact
store) and `docdoc.evaluation` does the scoring, unmodified.
"""

from __future__ import annotations

import math
import re
from typing import TYPE_CHECKING, Any

from docdoc.artifacts import ArtifactError
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
from docdoc.evaluation.values import carry, schema_facts, strip_indices
from docdoc.runs.model import RunStatus

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from docdoc.runs.corrections import Answer
    from docdoc.runs.model import Run

__all__ = ["answer_set", "field_types", "prediction_for", "typed", "typed_golden"]

# The registry declares `line_items.amount`; a label names an entry, `line_items[1].amount`.
_INDEX = re.compile(r"\[\d*\]")


def field_types(schema: Any) -> Mapping[str, str]:
    """Declared path -> type name for `schema` (a registry description), as the loader reads it."""
    return schema_facts([schema]).types_for(schema.identity)


def typed(value: Any, field_type: str | None) -> Any:
    """`value` as the Python type `field_type` declares, as the dataset loader types it.

    A check typed into the console arrives as a string, so a string is parsed
    first for the three types JSON would carry unquoted; `carry` does the rest.

    Raises:
        ValueError: the declared type cannot carry it.
    """
    if isinstance(value, str) and field_type in ("number", "integer", "boolean"):
        text = value.strip()
        if field_type == "boolean":
            if text.lower() not in ("true", "false"):
                raise ValueError(f"not a boolean: {value!r}")
            value = text.lower() == "true"
        elif field_type == "integer":
            value = int(text)
        else:
            value = float(text)
            if not math.isfinite(value):
                raise ValueError(f"not a finite number: {value!r}")
    return carry(value, field_type)


def _typed_or_raw(value: Any, field_type: str | None) -> Any:
    """Typed, or as stored when the type cannot carry it (it then matches nothing)."""
    try:
        return typed(value, field_type)
    except ValueError:
        return value


def typed_golden(golden: GoldenSet, types: Mapping[str, str]) -> GoldenSet:
    """`golden` with every label value typed, which a JSON round trip undoes."""
    return golden.model_copy(
        update={
            "labels": {
                blob_id: tuple(
                    label.model_copy(
                        update={
                            "value": _typed_or_raw(
                                label.value, types.get(strip_indices(label.field_path))
                            )
                        }
                    )
                    for label in labels
                )
                for blob_id, labels in golden.labels.items()
            }
        }
    )


def answer_set(answers: Sequence[Answer], *, tenant_id: str, schema: Any) -> GoldenSet:
    """The tenant's answers as a golden set over `schema`'s declared fields, typed by it."""
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
    golden = typed_golden(
        GoldenSet(documents=documents, labels={b: tuple(ls) for b, ls in by_blob.items()}),
        field_types(schema),
    )
    return golden.model_copy(update={"golden_set_id": golden_set_id_for(golden)})


def prediction_for(
    store: Any, run: Run, document_id: str, *, types: Mapping[str, str] | None = None
) -> DocumentPrediction:
    """What `run` predicted, from the tenant's artifact store, or how it failed.

    With `types`, the extracted values are typed as the prediction loader types
    them; without, they are as stored (what a drill-down shows).
    """
    if run.status is RunStatus.SUCCEEDED:
        try:
            loaded = _load(store, run.processing_id)
        except ArtifactError:
            # One corrupt artifact must not abort the whole evaluation.
            return _failed(document_id, Stage.VALIDATE, "artifact_unreadable")
        if loaded is not None:
            extraction = loaded["extraction"]
            if types is not None and extraction is not None:
                values = _typed_values(extraction.values, types)
                loaded["extraction"] = extraction.model_copy(update={"values": values})
            return DocumentPrediction(document_id=document_id, **loaded)
        return _failed(document_id, Stage.VALIDATE, "artifact_missing")

    try:
        stage = Stage(run.failed_stage) if run.failed_stage else Stage.PARSE
    except ValueError:
        stage = Stage.PARSE
    return _failed(document_id, stage, run.error_class or f"run_{run.status.value}")


def _typed_values(node: Any, types: Mapping[str, str]) -> Any:
    """The value tree with each `ExtractedValue`'s value typed by its declared path."""
    if isinstance(node, dict):
        return {name: _typed_values(child, types) for name, child in node.items()}
    if isinstance(node, list | tuple):
        return type(node)(_typed_values(child, types) for child in node)
    if hasattr(node, "field_path") and hasattr(node, "model_copy"):
        declared = types.get(strip_indices(node.field_path))
        return node.model_copy(update={"value": _typed_or_raw(node.value, declared)})
    return node


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

    # Only a validation artifact is a run's result; an OCR run's id is a parse.
    envelope = store.envelope(processing_id)
    if envelope is not None and envelope.stage != PipelineStage.VALIDATE.value:
        return None
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
