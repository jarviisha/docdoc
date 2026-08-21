"""Predictions — what the pipeline recorded, read and never re-derived.

A prediction is the recorded output of the pipeline for one document: its
extraction result, its grounding result, and its validation result, exactly as
those stages produced them. Nothing in this package re-derives, re-scores,
re-grounds, or re-validates any of it (FR-002). Evaluation reads recorded facts.

Two refusals live here, and both exist because the alternative is a confident
number computed over two things that do not describe the same subject:

- a prediction for a document the golden set does not contain (FR-005), and
- a prediction whose schema identity or hash differs from the labels' (FR-004).

A label written against ``invoice@1`` says nothing about a result produced under
``invoice@2``, and ADR-0008 is the reason: a major bump means a consumer contract
broke, so the two results are not describing the same fields.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from docdoc.evaluation.errors import EvaluationError, naming
from docdoc.evaluation.values import carry, strip_indices

# Imported at runtime, not under TYPE_CHECKING: pydantic resolves field
# annotations when it builds the model, so these names must actually exist. The
# same rule `pyproject.toml`'s `runtime-evaluated-base-classes` setting encodes.
#
# **From the submodules, never from the packages**, and this is load-bearing
# rather than stylistic. `docdoc.extraction/__init__.py` imports
# `adapter_registry`, which imports `adapters.gemini`, which imports
# `google.genai`. import-linter follows function-scoped imports, so
# `from docdoc.extraction import ExtractionResult` puts a provider SDK in this
# package's import graph and breaks the FR-007 contract. Reaching for the
# submodule does not. This is the convention `docdoc.grounding` and
# `docdoc.validation` already follow -- both import `docdoc.extraction.value`
# and `docdoc.extraction.schema`, neither imports `docdoc.extraction`.
from docdoc.extraction.extract import ExtractionResult
from docdoc.extraction.value import ExtractedValue
from docdoc.grounding.result import GroundingResult
from docdoc.validation.result import ValidationResult

if TYPE_CHECKING:
    from collections.abc import Mapping

    from docdoc.evaluation.golden import GoldenSet
    from docdoc.evaluation.values import SchemaFacts

__all__ = [
    "RECORDER_FILE",
    "DocumentPrediction",
    "PredictionSet",
    "Stage",
    "check_against",
    "load_prediction_set",
]

#: Where a recorded prediction set stamps the recorder's identity and version.
#: Named here rather than in :mod:`docdoc.recording` because the *reader* is the
#: one that must agree with the writer, and the reader is the layer that cannot
#: import the writer.
RECORDER_FILE = "recorder.json"


class Stage(StrEnum):
    """Where a document stopped, when it did not finish."""

    PARSE = "parse"
    EXTRACT = "extract"
    GROUND = "ground"
    VALIDATE = "validate"


class DocumentPrediction(BaseModel):
    """One document's recorded pipeline output (EVA-9)."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    document_id: str

    extraction: ExtractionResult | None = None
    grounding: GroundingResult | None = None
    validation: ValidationResult | None = None

    #: ``None`` when the document processed completely. Otherwise the stage it
    #: stopped at -- and its labelled fields count as *missing*, not as excluded
    #: (FR-037, EVA-9a). Failing on hard documents can never improve a score.
    failed_stage: Stage | None = None

    #: The typed error's class name. **Never a value**: an error message can
    #: quote the content it choked on, and this field travels into reports and
    #: logs where FR-057 forbids that.
    failure_reason: str | None = None

    #: Which parser produced the document these results describe. FR-040 requires
    #: a report to record the parser version, and this layer is the only place it
    #: could go missing: the scorer never opens a document, and an
    #: ``ExtractionResult`` does not carry its document's ingest provenance. So
    #: the recorder -- which does hold the ``Document`` -- copies it here.
    #:
    #: **Not part of ``prediction_set_id``.** ADR-0003 already folds the parser
    #: into the validation artifact id transitively, so hashing it again would
    #: add nothing and make a replayed set's identity depend on whether whoever
    #: replayed it happened to fill these in.
    parser_id: str | None = None
    parser_version: str | None = None

    @property
    def processed(self) -> bool:
        return self.failed_stage is None


class PredictionSet(BaseModel):
    """The recorded outputs for the documents of one golden set (EVA-10)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    predictions: dict[str, DocumentPrediction]
    recorder_id: str = ""
    recorder_version: str = ""
    prediction_set_id: str = ""

    def for_document(self, document_id: str) -> DocumentPrediction | None:
        return self.predictions.get(document_id)


def load_prediction_set(
    root: str | Path,
    *,
    facts: SchemaFacts | None = None,
    recorder_id: str = "",
    recorder_version: str = "",
) -> PredictionSet:
    """Read committed predictions from disk, one JSON file per document.

    This is the **replay** path, and it is the default for the public tier: the
    predictions were recorded once by :mod:`docdoc.recording`, committed, and are
    read back here by a contributor with no credentials and no network (FR-003).

    ``facts`` is what makes replay faithful rather than approximate -- see
    :func:`_rehydrate_values`. Without it the values come back as the JSON
    scalars they were serialized as, and every decimal, date, and datetime in the
    dataset compares unequal.
    """
    directory = Path(root)
    # The directory read, not an identity: a prediction set has none until it is
    # assembled, and the path is what a caller can act on.
    with naming(str(directory)):
        return _load_predictions(directory, facts, recorder_id, recorder_version)


def _load_predictions(
    directory: Path,
    facts: SchemaFacts | None,
    recorder_id: str,
    recorder_version: str,
) -> PredictionSet:
    predictions: dict[str, DocumentPrediction] = {}
    for file in sorted(directory.glob("*.json")):
        if file.name == RECORDER_FILE:
            continue
        payload = json.loads(file.read_text(encoding="utf-8"))
        prediction = _prediction_from_json(payload, source=file, facts=facts)
        if prediction.document_id in predictions:
            raise EvaluationError(
                f"two prediction files describe document {prediction.document_id!r}; "
                f"the second is {file.name}",
                document_id=prediction.document_id,
            )
        predictions[prediction.document_id] = prediction

    # The recorder's identity folds into `prediction_set_id` (EVA-24), so it is
    # committed next to the predictions rather than reconstructed by whoever
    # replays them -- a value a reader has to supply correctly is one that will
    # eventually be supplied incorrectly, and the identity would move for it.
    recorded = directory / RECORDER_FILE
    stamped = json.loads(recorded.read_text(encoding="utf-8")) if recorded.exists() else {}

    return PredictionSet(
        predictions=predictions,
        recorder_id=recorder_id or str(stamped.get("recorder_id", "")),
        recorder_version=recorder_version or str(stamped.get("recorder_version", "")),
    )


def _prediction_from_json(
    payload: Mapping[str, Any], *, source: Path, facts: SchemaFacts | None
) -> DocumentPrediction:
    raw = dict(payload)
    raw.pop("recorder_id", None)
    raw.pop("recorder_version", None)
    extraction = raw.get("extraction")
    if isinstance(extraction, dict):
        schema_identity = str(extraction.get("provenance", {}).get("schema_identity", ""))
        types = None if facts is None else facts.types_for(schema_identity)
        extraction = dict(extraction)
        extraction["values"] = _rehydrate_values(extraction.get("values", {}), types, prefix="")
        raw["extraction"] = extraction

    try:
        return DocumentPrediction.model_validate(raw)
    except Exception as exc:
        raise EvaluationError(
            f"prediction file {source.name} is not well formed: {exc}",
            document_id=str(raw.get("document_id", "")) or None,
        ) from exc


def _rehydrate_values(node: Any, types: Mapping[str, str] | None, *, prefix: str) -> Any:
    """Rebuild the value tree pydantic could not, and retype what JSON flattened.

    ``ExtractionResult.values`` is ``dict[str, Any]``, so pydantic reads a
    serialized tree back as plain dictionaries: the scorer's walk would find no
    ``field_path`` anywhere and score a complete prediction as entirely missing.
    That is one of two losses, and the quieter one is worse -- ``Decimal`` and the
    date types serialize to strings, and a string that comes back a string fails
    `comparators@1`'s type gate against a correctly typed label (EVA-12a). Both
    are repaired here, using the **same** rules the label loader uses, because
    the moment those two coercions disagree is the moment every decimal in the
    dataset reads as an extraction error.
    """
    if isinstance(node, dict) and "field_path" in node and "present" in node:
        field = dict(node)
        declared = (types or {}).get(strip_indices(str(field.get("field_path", ""))))
        if declared is not None and field.get("value") is not None:
            field["value"] = carry(field["value"], declared)
        return ExtractedValue.model_validate(field)
    if isinstance(node, dict):
        return {
            name: _rehydrate_values(child, types, prefix=f"{prefix}.{name}" if prefix else name)
            for name, child in node.items()
        }
    if isinstance(node, (list, tuple)):
        return tuple(
            _rehydrate_values(child, types, prefix=f"{prefix}[{index}]")
            for index, child in enumerate(node)
        )
    return node


def check_against(predictions: PredictionSet, golden: GoldenSet) -> None:
    """Refuse a prediction set that does not describe this golden set.

    Deliberately *not* symmetric. A prediction for an unknown document is refused
    (FR-005) because the two sides do not describe the same thing. A golden-set
    document with **no** prediction is not refused: it is reported as
    ``UNEVALUATED`` and stays in every denominator, because dropping it is how a
    crash becomes an accuracy improvement.
    """
    with naming(golden.golden_set_id or None):
        _check(predictions, golden)


def _check(predictions: PredictionSet, golden: GoldenSet) -> None:
    for document_id, prediction in sorted(predictions.predictions.items()):
        document = golden.document(document_id)
        if document is None:
            raise EvaluationError(
                f"prediction supplied for document {document_id!r}, which the "
                "golden set does not contain; the two sides do not describe the "
                "same thing",
                document_id=document_id,
            )

        extraction = prediction.extraction
        if extraction is None:
            continue
        provenance = extraction.provenance
        identity = provenance.schema_identity
        schema_hash = provenance.schema_hash

        if identity != document.schema_identity:
            raise EvaluationError(
                f"document {document_id!r} was labelled under schema "
                f"{document.schema_identity} but predicted under {identity}; a "
                "label written under one schema version says nothing about a "
                "result produced under another (ADR-0008)",
                document_id=document_id,
                expected=document.schema_identity,
                actual=identity,
            )
        if schema_hash != document.schema_hash:
            raise EvaluationError(
                f"document {document_id!r} was labelled under schema hash "
                f"{document.schema_hash} but predicted under {schema_hash}; a "
                "result-affecting schema edit invalidates the labels written "
                "against it (ADR-0008)",
                document_id=document_id,
                expected=document.schema_hash,
                actual=schema_hash,
            )
