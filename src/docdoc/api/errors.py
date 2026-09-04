"""Every failure as a stable, provider-neutral, typed status.

Two rules make up this module.

**A document that fails validation is not an error.** The run succeeded; the
answer is that the document is wrong. It comes back ``200`` with an invalid
verdict, exactly as the library returns it. Anything else would make a caller
unable to tell "your invoice is wrong" from "docdoc is broken" — the same
distinction the command line spends its ``0``/``1`` split on.

**A provider's message never crosses the wire.** It may quote the document it
choked on, and an error body is the single most likely thing to be pasted into a
ticket. docdoc's own message does travel: it is written by this project, names
identities and configuration rather than content, and is the difference between
a debuggable failure and a status code.

**A mid-run failure carries what the run had already produced** (FR-066). A
failed run has no terminal artifact and therefore no job to fetch later, so this
response is the only place those results can appear. Dropping them here would
honour FR-004 in the library and defeat it at the boundary.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docdoc.api.models import ErrorBody, ErrorDetail, StageOutcomeView

if TYPE_CHECKING:
    from docdoc.pipeline import PipelineResult

__all__ = [
    "STATUS_BY_ERROR",
    "body_for_exception",
    "body_for_failed_run",
    "status_for",
    "status_for_failed_run",
]

#: ``contracts/http-api.md`` §6, as data.
#:
#: Keyed by class **name** rather than by class, so that reading this does not
#: import five layers, and so that an error from a layer whose extra is not
#: installed still maps. The names are stable: the constitution's error model
#: fixes them, and FR-050 forbids wrapping them in new ones.
STATUS_BY_ERROR: dict[str, int] = {
    # The document itself was refused, before any stage ran.
    "UnsupportedDocumentError": 415,
    # Configuration the caller controls.
    "SchemaError": 400,
    "ParserCapabilityError": 422,
    # A stage rejected what it was given.
    "ExtractionError": 422,
    "GroundingError": 422,
    "ValidationError": 422,
    "DocumentError": 422,
    "DocumentInvariantError": 422,
    "SpanError": 422,
    "GeometryError": 422,
    "IdentityError": 422,
    # Somebody else's service failed.
    "ProviderError": 502,
    "ModelProviderError": 502,
    # A parser produced something invalid, which is docdoc's problem, not the
    # caller's — the file was accepted and then mishandled.
    "ParserError": 500,
    # A stored artifact failed its integrity check, or the run could not be
    # sequenced. Both are this deployment's fault.
    "ArtifactError": 500,
    "PipelineError": 500,
}

#: Anything typed but unmapped. 500 rather than 400, deliberately: an error class
#: this table does not know about is docdoc failing to describe its own failure,
#: and blaming the caller for it would be wrong and unhelpful at once.
_DEFAULT_STATUS = 500

#: ``UnsupportedDocumentError`` covers both "wrong type" and "too large", and the
#: two have different statuses. The error carries a ``reason``; this maps it,
#: rather than parsing a message.
_UNSUPPORTED_BY_REASON = {
    "size": 413,
    "too_large": 413,
    "page_count": 413,
    "media_type": 415,
    "type": 415,
}


#: ``ArtifactError`` is the same shape of problem: one class, several conditions,
#: and one of them is not a server fault at all. A store that cannot be *reached*
#: is a dependency outage — retryable, and the caller should be told so — while a
#: corrupt envelope or a divergent write really is a 500.
#:
#: Without this, an unreachable store surfaced as ``500`` from the metadata route
#: and as ``404 UnknownBlob`` from run submission, which told a caller their
#: document was gone when the store was merely down. The blob stores stopped
#: conflating absent with unreachable; this is the same distinction arriving at
#: the boundary that reports it.
#: `not_configured` is deliberately **not** here, and the existing contract test
#: was right to refuse it. A store nobody configured is a deployment fault, not a
#: transient one: 503 tells the caller to retry, and retrying will not conjure a
#: store. That stays 500, which is what `contracts/http-api.md` §6 documents.
_ARTIFACT_BY_REASON = {
    "unavailable": 503,
}


def status_for(error: BaseException) -> int:
    """The HTTP status this typed error maps to."""
    name = type(error).__name__
    reason = str(getattr(error, "reason", "") or "")
    if name == "UnsupportedDocumentError":
        return _UNSUPPORTED_BY_REASON.get(reason, 415)
    if name == "ArtifactError" and reason in _ARTIFACT_BY_REASON:
        return _ARTIFACT_BY_REASON[reason]
    return STATUS_BY_ERROR.get(name, _DEFAULT_STATUS)


def _detail_of(error: BaseException, *, stage: str | None) -> ErrorDetail:
    """The typed error, with the structured fields it carries and nothing else.

    Read field by field from a fixed list rather than by sweeping ``__dict__``,
    so that a field added to an error class in some later milestone cannot arrive
    in an HTTP body without somebody deciding it should. Every name here is an
    identifier, a version, or a configuration key — never a value from the
    document.
    """
    detail = {
        name: getattr(error, name)
        for name in (
            "reason",
            "artifact_id",
            "document_id",
            "blob_id",
            "schema_identity",
            "identity",
            "adapter_id",
            "parser_id",
            "field_path",
            "rule",
            "available",
        )
        if getattr(error, name, None) is not None
    }
    return ErrorDetail(
        error_class=type(error).__name__,
        stage=stage,
        # docdoc's own message. A provider's is never repeated: the adapters
        # translate provider exceptions into this error model before they reach
        # here, which is where that rule is actually enforced (Principle IV).
        message=str(error),
        detail={key: _plain(value) for key, value in detail.items()},
    )


def _plain(value: Any) -> Any:
    """Reduce a detail field to something JSON can carry."""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return str(value)


def body_for_exception(error: BaseException, *, stage: str | None = None) -> ErrorBody:
    """An error that stopped the request before a run produced anything."""
    return ErrorBody(error=_detail_of(error, stage=stage))


def body_for_failed_run(result: PipelineResult, error: BaseException | None = None) -> ErrorBody:
    """A mid-run failure, with the results of the stages that succeeded (FR-066).

    The stage at fault is read off the result rather than from the exception,
    because the pipeline has already attributed it to the layer that *declared*
    the error rather than to whatever was executing when it surfaced (FR-005).
    """
    stage = None if result.failed_stage is None else result.failed_stage.value

    if error is not None:
        detail = _detail_of(error, stage=stage)
    else:
        # The pipeline records a failure without re-raising, so there is often no
        # exception to read. The class name is what it kept — deliberately, since
        # the message could quote the document — and it is enough to name the
        # failure.
        outcome = None if result.failed_stage is None else result.outcome_for(result.failed_stage)
        failure_class = (outcome.failure_class if outcome else None) or "PipelineError"
        detail = ErrorDetail(
            error_class=failure_class,
            stage=stage,
            message=f"the run failed at the {stage or 'unknown'} stage",
        )

    return ErrorBody(
        error=detail,
        outcomes=tuple(
            StageOutcomeView(
                stage=outcome.stage.value,
                status=outcome.status.value,
                artifact_id=outcome.artifact_id,
                duration_ms=outcome.duration_ms,
                failure_class=outcome.failure_class,
            )
            for outcome in result.outcomes
        ),
        results=_surviving(result),
    )


def _surviving(result: PipelineResult) -> dict[str, Any]:
    """Whatever the stages before the failure produced.

    Keyed by stage name so a caller can tell "grounding ran and found nothing"
    from "grounding never ran" — an omitted key and a key holding an empty result
    are different facts, and the outcomes list says which.

    **The parsed document is represented by its identity, not its content.** It
    carries every token and every bounding box, which is megabytes on a long
    document and the largest thing in the run — and it is already addressed: the
    parse stage's ``artifact_id`` is in ``outcomes``, and a caller who wants the
    document can fetch it. Putting it inline would make every mid-run failure
    response an order of magnitude larger than the successful one.
    """
    surviving: dict[str, Any] = {}
    for name, value in (
        ("extract", result.extraction),
        ("ground", result.grounding),
        ("validate", result.validation),
    ):
        if value is not None:
            surviving[name] = value.model_dump(mode="json")
    return surviving


def status_for_failed_run(result: PipelineResult) -> int:
    """The status a mid-run failure maps to, from the class the run recorded."""
    outcome = None if result.failed_stage is None else result.outcome_for(result.failed_stage)
    name = (outcome.failure_class if outcome else None) or "PipelineError"
    if name == "UnsupportedDocumentError":
        # The reason is not on the result — only the class name is kept. 415 is
        # the safer of the two: a caller told "unsupported type" for an oversized
        # file will look at the file, which is the right place either way.
        return 415
    return STATUS_BY_ERROR.get(name, _DEFAULT_STATUS)
