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
    "SchemaChoice",
    "SchemaListing",
    "StageOutcomeView",
    "StorelessRunResponse",
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


class SchemaChoice(BaseModel):
    """One schema a deployment has configured.

    Carries the identity and nothing else. Not trimmed for tidiness: this
    endpoint is unauthenticated like the rest of the interface, and a filesystem
    layout is not something to hand out for free (FR-011). Field descriptions
    exist — ``SchemaRegistry.describe()`` returns them — and publishing schema
    internals to serve a choice that needs only a string would be a poor trade.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: A concrete ``name@version``, exactly the form ``POST /v1/extract`` accepts
    #: and ``SchemaRegistry.resolve()`` resolves, so a listed schema is runnable
    #: without translation (FR-010).
    identity: str


class SchemaListing(BaseModel):
    """What ``GET /v1/schemas`` returns.

    **An empty list is success** (FR-012). A deployment with no schemas
    configured is validly configured — it just has nothing to offer — and
    reporting that as an error would send a caller looking for a fault that is
    not there. The viewer names the setting that populates it instead.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    schemas: tuple[SchemaChoice, ...] = ()


class _RunFields(BaseModel):
    """Everything a run reports except its identity.

    Extracted so that ``StorelessRunResponse`` is *literally* "the run response
    minus ``job_id``" rather than a second model that has to be kept in step with
    the first. A field added here reaches both surfaces; a field added to one of
    the subclasses is a deliberate difference between them, which is the only
    difference either should have.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

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


class RunResponse(_RunFields):
    """What a run returns: the identity **and** the result (FR-067).

    Returning only the identity would be a receipt the caller often cannot
    redeem. With no store configured the terminal artifact is never written, and
    after a degraded write it is written nowhere — in both cases the run
    succeeded, the result existed, and this response is the only copy of it.
    """

    #: The terminal artifact id, which is ADR-0003's ``processing_id`` and the
    #: job id. Not a second identifier (FR-033).
    job_id: str


class StorelessRunResponse(_RunFields):
    """What ``POST /v1/extract`` returns: the result, and no job (FR-003).

    **The missing field is the point.** A storeless run writes no terminal
    artifact, and ADR-0003's ``processing_id`` *is* the terminal artifact id — so
    there is no identity to hand back and nothing to fetch later. Returning
    ``job_id: null`` would be the same statement; omitting the field is the
    stronger one, because a caller cannot then pass it to ``GET /v1/jobs/{id}``
    and receive ``unknown`` for an identity we invented.

    A caller who wants a retrievable identity submits the document first and uses
    the store-backed route, which is unchanged (FR-001, contracts §2).
    """


class ErrorDetail(BaseModel):
    """The typed error, named and attributed.

    Carries docdoc's own message and never a provider's, which may quote the
    document it choked on (FR-037).
    """

    # Built as `error_class=` in Python and serialised as `class`, which is what
    # the contract's example shows and what a caller reads. `class` is a keyword,
    # so the two names are the only way to have both.
    #
    # A **serialization** alias rather than a plain one, because that is the
    # narrower statement of the intent and the only one that type-checks. A plain
    # `alias` also renames the constructor parameter — `dataclass_transform`,
    # which is how mypy reads a pydantic model, then sees `ErrorDetail(class=...)`
    # as the only valid call and rejects every real one. `populate_by_name` fixes
    # that at runtime and is invisible to the type checker, which is why
    # `mypy src/docdoc` flagged both call sites for a model that has always worked.
    # Nothing validates an `ErrorDetail` *from* a payload, so the input alias was
    # never needed: this type is constructed and serialised, never parsed.
    model_config = ConfigDict(frozen=True, extra="forbid", serialize_by_alias=True)

    error_class: str = Field(serialization_alias="class")
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


class RunAcceptedResponse(BaseModel):
    """What ``POST /v1/documents/{blob_id}/runs`` returns, before anything ran.

    **No ``processing_id`` field at all** — absent, not null. ADR-0012 §3 set the
    precedent for the same reason: a null invites the caller to send it to
    ``GET /v1/jobs/{id}``, which would answer ``unknown`` about an identity
    nobody issued. Omitting the field ends that conversation one step earlier.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    status: str
    created_at: str


class RunStateResponse(BaseModel):
    """What ``GET /v1/runs/{run_id}`` returns.

    ``tenant_id`` and ``idempotency_key`` are absent by construction: the model
    is built from ``Run.dump_public()``, which excludes both. Returning the first
    would give one tenant a value to compare against another's, and SC-008 wants
    cross-tenant responses byte-identical to non-existence.

    ``failed_stage`` is ``None`` on a run that failed before reaching a stage —
    an unresolvable schema, under FR-091 — and that is a real distinction rather
    than a missing value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    run_id: str
    status: str
    #: The caller's own submission, echoed back. Not another tenant's anything:
    #: a run is only readable under the tenant that owns it, so returning what
    #: was submitted discloses nothing the submitter did not send.
    blob_id: str
    schema_identity: str
    attempts: int = Field(ge=0)
    created_at: str
    updated_at: str
    expires_at: str

    processing_id: str | None = None
    failed_stage: str | None = None
    error_class: str | None = None
    worker_id: str | None = None
    lease_until: str | None = None
    request_id: str | None = None
    stage_outcomes: tuple[dict[str, Any], ...] = ()
