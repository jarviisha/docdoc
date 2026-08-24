"""What the HTTP interface accepts and returns.

Thin by design. Every response here is a view over a model some layer below
already produced, and the one thing this module decides is *which* fields cross
the wire — never what they mean.

**The submission response carries ``blob_id``, not ``document_id``.** The
founding sketch had it the other way. Under ADR-0002 a ``document_id`` identifies
*one parse* of a file, and at submission no parse has happened or even been
chosen, so returning a blob id under that name would hand a caller an identifier
whose spans and geometry anchor to nothing (research R8).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "BlobMetadata",
    "ErrorBody",
    "ErrorDetail",
    "JobStatus",
    "JobStatusResponse",
    "RunResponse",
    "StageOutcomeView",
    "SubmissionResponse",
]


class SubmissionResponse(BaseModel):
    """What ``POST /v1/documents`` returns."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blob_id: str
    size_bytes: int = Field(ge=0)
    media_type: str


class BlobMetadata(BaseModel):
    """What ``GET /v1/documents/{blob_id}`` returns.

    Identity, size, and detected media type. Not the bytes: this endpoint answers
    "do you have this, and what is it", and returning the document itself would
    make a metadata call a way to read every document the deployment holds.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    blob_id: str
    size_bytes: int = Field(ge=0)
    media_type: str | None = None


class StageOutcomeView(BaseModel):
    """One stage's fate, as a caller sees it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: str
    status: str
    artifact_id: str | None = None
    duration_ms: int = 0
    #: The typed error's **class name**. Never its message (FR-043).
    failure_class: str | None = None


class JobStatus(StrEnum):
    """The closed set, and there are three (FR-035).

    ``unavailable`` deliberately does **not** distinguish *never produced* from
    *produced and since cleared*. The store is content-addressed and append-only,
    ``clear()`` leaves no tombstone, and nothing records what the store was never
    asked to hold — so the two are one observation, and a status claiming to tell
    them apart would be inventing the difference (ADR-0010, amended 2026-08-24).

    There is no ``pending``. Fabricating one for an id nobody issued is how a
    client waits forever.
    """

    SUCCEEDED = "succeeded"
    #: Well-formed, and not in this store.
    UNAVAILABLE = "unavailable"
    #: Not a well-formed artifact identity, so no run could have produced it.
    UNKNOWN = "unknown"


class JobStatusResponse(BaseModel):
    """What ``GET /v1/jobs/{job_id}`` returns."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    job_id: str
    status: JobStatus
    #: Why, in one sentence, for the two statuses that are not ``succeeded``.
    detail: str | None = None


class RunResponse(BaseModel):
    """What a run returns: the identity **and** the result (FR-067).

    Returning only the identity would be a receipt the caller often cannot
    redeem. With no store configured the terminal artifact is never written, and
    after a degraded write it is written nowhere — in both cases the run
    succeeded, the result existed, and this response is the only copy of it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The terminal artifact id, which is ADR-0003's ``processing_id`` and the
    #: job id. Not a second identifier (FR-033).
    job_id: str
    document_id: str | None = None
    schema_identity: str
    verdict: str | None = None

    outcomes: tuple[StageOutcomeView, ...] = ()
    #: The three stage results, serialised. ``document`` is deliberately absent:
    #: `PipelineResult` excludes it, and every value, verdict, location, and
    #: identity FR-034 requires lives on the other three.
    extraction: Any = None
    grounding: Any = None
    validation: Any = None


class ErrorDetail(BaseModel):
    """The typed error, named and attributed.

    Carries docdoc's own message and never a provider's, which may quote the
    document it choked on (FR-037).
    """

    # `populate_by_name` so the field can be built as `error_class=` in Python
    # and still serialise as `class`, which is what the contract's example shows
    # and what a caller reads. `class` is a keyword, so the alias is the only way
    # to have both.
    model_config = ConfigDict(
        frozen=True, extra="forbid", populate_by_name=True, serialize_by_alias=True
    )

    error_class: str = Field(alias="class")
    stage: str | None = None
    message: str
    detail: dict[str, Any] = Field(default_factory=dict)


class ErrorBody(BaseModel):
    """A failure, plus everything the run had already produced (FR-066).

    A failed run produces no terminal artifact and therefore no job to fetch
    later, so this response is the **only** place a partial result can appear.
    Without it FR-004's "MUST NOT discard partial results" would be honoured in
    the library and defeated one layer out.

    ``results`` legitimately carries extracted values: it is the caller's own
    document coming back on the caller's own request, which is a different thing
    from a log line. FR-043's prohibition is about logs.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    error: ErrorDetail
    outcomes: tuple[StageOutcomeView, ...] = ()
    results: dict[str, Any] = Field(default_factory=dict)
