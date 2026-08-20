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

from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict

from docdoc.evaluation.errors import EvaluationError

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
from docdoc.grounding.result import GroundingResult
from docdoc.validation.result import ValidationResult

if TYPE_CHECKING:
    from docdoc.evaluation.golden import GoldenSet

__all__ = ["DocumentPrediction", "PredictionSet", "Stage", "check_against"]


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


def check_against(predictions: PredictionSet, golden: GoldenSet) -> None:
    """Refuse a prediction set that does not describe this golden set.

    Deliberately *not* symmetric. A prediction for an unknown document is refused
    (FR-005) because the two sides do not describe the same thing. A golden-set
    document with **no** prediction is not refused: it is reported as
    ``UNEVALUATED`` and stays in every denominator, because dropping it is how a
    crash becomes an accuracy improvement.
    """
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
