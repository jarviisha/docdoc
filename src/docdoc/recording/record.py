"""Running the pipeline over a golden set and recording what came back.

This is the only part of Milestone 6 that reaches a provider, and it is opt-in:
nothing in :mod:`docdoc.evaluation` calls it, and scoring never requires it.
Public-tier predictions are recorded once, committed, and replayed, so a
contributor scores a checkout with no credentials at all (FR-003).

**A document that fails is recorded, never dropped.** That is the single rule
this module exists to hold. Dropping a document that raised is the obvious
implementation -- it reads as robustness, the loop keeps going, nothing is lost
that anyone can see. What it does is remove that document from every denominator,
so the pipeline's score rises for having crashed on it. The failure is recorded
with its stage and the typed error's **class name**, never a value: an exception
message can quote the content it choked on, and this field travels into reports
and logs where FR-057 forbids that.

**This module no longer sequences the stages.** It calls
:func:`docdoc.pipeline.run`, which is the only place parse → extract → ground →
validate is written down (FR-009, SC-014). Until Milestone 7 the order lived
*here* — inside one private function of a package that exists to serve
evaluation — so the order in which docdoc processes a document lived in the
module least likely to be read by somebody asking what the order is. Two
definitions of it in one repository is the condition FR-009 exists to prevent,
and this is the half that was deleted rather than the half that was added.

**The recorder runs with no store by default**, and that is a deliberate choice
rather than an oversight. A committed prediction set is the product of full
execution, every time — so a stale artifact can never quietly move a published
metric. The store is a cost optimisation a caller may opt into for a corpus
sweep; it is never an input to the numbers of record.

The known limitation this docstring carried for a milestone — no ``ArtifactStore``,
so every run re-parsed every document — is closed. Milestone 7 built the store,
and :mod:`docdoc.grounding` now caches its match view by
``document_id`` + ``match_view_version`` as ADR-0006 always specified.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from docdoc.evaluation import (
    DocumentPrediction,
    PredictionSet,
    Stage,
    Tier,
)
from docdoc.evaluation.predictions import RECORDER_FILE

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

    from docdoc.evaluation import GoldenDocument, GoldenSet
    from docdoc.kernel import Document

__all__ = [
    "RECORDER_ID",
    "RECORDER_VERSION",
    "record_predictions",
    "write_prediction_set",
]

#: Folded into ``prediction_set_id`` (EVA-10). This is why recording is a package
#: rather than a script under ``examples/``: a script has no version, so the
#: prediction set's identity would carry a hole exactly where FR-040 and FR-042
#: need it whole.
RECORDER_ID = "pipeline-recorder"
RECORDER_VERSION = "1.0.0"


class _Adapter(Protocol):
    """The bit of ``ModelAdapter`` this module needs.

    Structural rather than imported, so the type of a provider adapter does not
    reach this module's signature -- Principle IV keeps those inside adapters.
    """

    @property
    def id(self) -> str: ...


def _stage_of(error: BaseException) -> Stage:
    """Which stage an exception came from, by its declaring layer.

    Read off the exception's own module rather than from where it was caught,
    because a grounding error raised during validation is still a grounding
    error -- and attributing it to the wrong stage would send whoever reads the
    report to the wrong code.
    """
    module = type(error).__module__
    if ".ingest" in module:
        return Stage.PARSE
    if ".extraction" in module:
        return Stage.EXTRACT
    if ".grounding" in module:
        return Stage.GROUND
    return Stage.VALIDATE


def record_predictions(
    golden: GoldenSet,
    *,
    adapter: _Adapter,
    registry: Any,
    documents: Mapping[str, Document] | Callable[[GoldenDocument], Document] | None = None,
    root: str | Path | None = None,
    include_restricted: bool = False,
) -> PredictionSet:
    """Run parse -> extract -> ground -> validate over a golden set (contracts §7).

    Args:
        golden: The documents to record predictions for.
        adapter: The model adapter. The ``echo`` adapter records the public tier
            reproducibly and offline; a provider adapter is needed only for a
            restricted corpus.
        registry: A ``SchemaRegistry`` resolving each document's
            ``schema_identity``.
        documents: Already-parsed documents, by ``document_id``, or a callable
            producing one. Supplying them **skips the parse**, which is how the
            offline suite records without a PDF reader -- and how a caller who
            already holds parsed documents avoids re-parsing them, since there is
            no ``ArtifactStore`` to do it for them.
        root: Where the documents live, when they must be parsed. Each
            document's ``path`` is resolved against this.
        include_restricted: Whether to record the restricted tier. Off by
            default, because that tier needs a corpus this process may not have.

    Returns:
        One ``DocumentPrediction`` per document attempted, failures included.
    """
    predictions: dict[str, DocumentPrediction] = {}

    for document in sorted(golden.documents, key=lambda d: d.document_id):
        if document.tier is Tier.RESTRICTED and not include_restricted:
            continue
        predictions[document.document_id] = _record_one(
            document,
            adapter=adapter,
            registry=registry,
            documents=documents,
            root=root,
        )

    return PredictionSet(
        predictions=predictions,
        recorder_id=RECORDER_ID,
        recorder_version=RECORDER_VERSION,
    )


def _resolve_document(
    document: GoldenDocument,
    documents: Mapping[str, Document] | Callable[[GoldenDocument], Document] | None,
    root: str | Path | None,
) -> Document:
    if documents is not None:
        if callable(documents):
            return documents(document)
        resolved = documents.get(document.document_id)
        if resolved is not None:
            return resolved

    if document.path is None:
        raise FileNotFoundError(
            f"document {document.document_id!r} declares no path and none was supplied"
        )

    # Imported here rather than at module scope. `docdoc.ingest`'s package
    # `__init__` reaches its parser registry and, through it, PyMuPDF and the
    # Azure SDK; a caller who supplies parsed documents should not need either
    # installed to record a prediction set.
    from docdoc.ingest import parse

    source = Path(root) / document.path if root is not None else Path(document.path)
    return parse(source.read_bytes())


def _record_one(
    document: GoldenDocument,
    *,
    adapter: _Adapter,
    registry: Any,
    documents: Mapping[str, Document] | Callable[[GoldenDocument], Document] | None,
    root: str | Path | None,
) -> DocumentPrediction:
    """One document through the pipeline, with whatever it reached recorded.

    The stage sequence is :mod:`docdoc.pipeline`'s and is not restated here
    (FR-009). What this function still owns is the *recording* rule, which is
    evaluation's rather than the pipeline's: a document that fails is recorded,
    never dropped. Dropping it is the obvious implementation — it reads as
    robustness, the loop keeps going, nothing is lost that anyone can see — and
    what it does is remove that document from every denominator, so the pipeline's
    score rises for having crashed on it.

    A failure keeps everything already produced, which the pipeline guarantees
    (FR-004): a document that fails at ``VALIDATE`` is recorded with its
    extraction and its grounding, because those are real results and discarding
    them would lose the evidence of what went wrong.
    """
    from docdoc.pipeline import run

    parsed: Document | None = None

    try:
        parsed = _resolve_document(document, documents, root)
        result = run(
            schema=document.schema_identity,
            registry=registry,
            adapter=adapter,
            # No store. A committed prediction set is the product of full
            # execution, so a stale artifact can never move a published metric.
            #
            # No `source` either: the document is already parsed, so there is
            # nothing to read. It used to be passed here positionally *as well*,
            # which type-checked as `SourceFile | bytes` receiving a `Document`
            # and was simply never read.
            document=parsed,
        )
    # Resolving the document can still raise — the pipeline never sees a file it
    # was not given. Every other failure is *recorded* by the pipeline rather
    # than raised, and is read off the result below.
    except Exception as error:
        return DocumentPrediction(
            document_id=document.document_id,
            failed_stage=_stage_of(error),
            # The class name, never the message. An exception message can quote
            # the content it choked on, and this field reaches reports and logs.
            failure_reason=type(error).__name__,
            parser_id=None if parsed is None else parsed.provenance.parser_id,
            parser_version=None if parsed is None else parsed.provenance.parser_version,
        )

    return DocumentPrediction(
        document_id=document.document_id,
        extraction=result.extraction,
        grounding=result.grounding,
        validation=result.validation,
        failed_stage=_recording_stage(result),
        failure_reason=_failure_reason(result),
        parser_id=parsed.provenance.parser_id,
        parser_version=parsed.provenance.parser_version,
    )


def _recording_stage(result: Any) -> Stage | None:
    """The failed stage, translated into evaluation's own ``Stage``.

    Two same-named enums with the same four members, deliberately not merged:
    evaluation's is folded into ``prediction_set_id``, so unifying them would
    move the identity of the committed public tier and invalidate a dataset for a
    tidiness gain. This is the one place they meet, and it is three lines.
    """
    if result.failed_stage is None:
        return None
    return Stage(result.failed_stage.value)


def _failure_reason(result: Any) -> str | None:
    if result.failed_stage is None:
        return None
    outcome = result.outcome_for(result.failed_stage)
    return None if outcome is None else outcome.failure_class


def write_prediction_set(predictions: PredictionSet, root: str | Path) -> Path:
    """Commit a prediction set to disk, one JSON file per document.

    The recorder's identity is stamped alongside rather than folded into each
    file, because it belongs to the set: ``prediction_set_id`` reads it, and a
    value whoever replays the set has to supply by hand is one that will
    eventually be supplied wrongly -- moving the identity for it.
    """
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)

    for document_id, prediction in sorted(predictions.predictions.items()):
        name = document_id.replace(":", "_")
        (directory / f"{name}.json").write_text(
            prediction.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )

    (directory / RECORDER_FILE).write_text(
        json.dumps(
            {
                "recorder_id": predictions.recorder_id,
                "recorder_version": predictions.recorder_version,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return directory
