"""Five endpoints, one synchronous run each, and no state of its own.

The run happens inside the request. There is no queue, no worker pool, no
background executor, and no job table — not as a simplification of an
asynchronous design, but because the identity model does not permit one. A job id
that *is* the terminal artifact id cannot be issued before the run, since that id
is not knowable until the stages feeding it have finished (research R7). Running
inside the request dissolves the problem: by the time there is something to hand
back, the id exists.

**A store is a deployment decision, and two endpoints need one** (FR-068).
Submission has nowhere to put bytes without it, and a job lookup is definitionally
a store lookup. Running an extraction and reading its result do not, because the
run's response carries the result (FR-067).

**Limits are enforced in two places, and both are necessary.** The request body
cap is applied while reading, before the body is buffered — the one limit
``ingest.Limits`` cannot know about, because by the time bytes reach it they are
already in memory (research R10). Document size and the media-type allowlist are
``ingest.Limits``'s, reused rather than restated, and are checked from the bytes
and never from a client-declared type.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from docdoc.api import errors as api_errors
from docdoc.api.models import (
    BlobMetadata,
    JobStatus,
    JobStatusResponse,
    RunResponse,
    StageOutcomeView,
    SubmissionResponse,
)
from docdoc.api.settings import (
    DEFAULT_MAX_REQUEST_BYTES,
    REQUEST_BYTES_ENV,
    SCHEMA_PATHS_ENV,
    STORE_ROOT_ENV,
)

if TYPE_CHECKING:
    from docdoc.artifacts import ArtifactStore, BlobStore
    from docdoc.pipeline import PipelineResult

__all__ = ["build_app", "create_app"]


class _Deployment:
    """What this service was configured with, resolved once.

    Held on the app rather than read per request, so that "is there a store?" has
    one answer for the lifetime of the process and a test can substitute one
    without touching the environment.
    """

    def __init__(
        self,
        *,
        store: ArtifactStore | None = None,
        blobs: BlobStore | None = None,
        registry: Any = None,
        adapter: Any = None,
        max_request_bytes: int | None = None,
        limits: Any = None,
    ) -> None:
        self.store = store
        self.blobs = blobs
        self._registry = registry
        self._adapter = adapter
        self.limits = limits
        self.max_request_bytes = max_request_bytes or _configured_request_cap()

    @property
    def has_store(self) -> bool:
        return self.blobs is not None

    def registry(self) -> Any:
        if self._registry is not None:
            return self._registry
        from docdoc.extraction import SchemaRegistry

        raw = os.environ.get(SCHEMA_PATHS_ENV, "")
        return SchemaRegistry.from_paths([p for p in raw.split(os.pathsep) if p.strip()])

    def adapter(self) -> Any:
        if self._adapter is not None:
            return self._adapter
        from docdoc.extraction.adapter_registry import default_adapter

        return default_adapter()

    def artifact_store(self) -> Any:
        from docdoc.artifacts import NullArtifactStore

        return self.store or NullArtifactStore()


def _configured_request_cap() -> int:
    raw = os.environ.get(REQUEST_BYTES_ENV, "").strip()
    if not raw:
        return DEFAULT_MAX_REQUEST_BYTES
    try:
        return max(1, int(raw))
    except ValueError:
        return DEFAULT_MAX_REQUEST_BYTES


def _default_deployment() -> _Deployment:
    """Read the environment once, at startup.

    There is no default store root (FR-017, FR-044), so a service started with
    nothing configured runs every stage every time and refuses submissions. That
    is the honest behaviour: the artifacts hold extracted values and the blobs
    hold whole documents, and where those land is an operator's decision.
    """
    from docdoc.artifacts import BlobStore, FileArtifactStore

    root = os.environ.get(STORE_ROOT_ENV, "").strip()
    if not root:
        return _Deployment()
    return _Deployment(store=FileArtifactStore(root), blobs=BlobStore(root))


def build_app(deployment: _Deployment | None = None) -> FastAPI:
    """The application, with its configuration injected or read from the environment."""
    app = FastAPI(
        title="docdoc",
        summary="Structured, validated, traceable data out of documents.",
        version="0.1.0",
    )
    app.state.deployment = deployment or _default_deployment()
    app.include_router(_router())
    _install_error_handler(app)
    return app


def create_app() -> FastAPI:
    """The ASGI factory, for ``uvicorn docdoc.api.app:create_app --factory``."""
    return build_app()


def _deployment_of(request: Request) -> _Deployment:
    return request.app.state.deployment  # type: ignore[no-any-return]


def _router() -> APIRouter:
    router = APIRouter(prefix="/v1")

    @router.post("/documents", response_model=SubmissionResponse)
    async def submit(request: Request) -> Any:
        """Store source bytes and return their identity.

        Idempotent by construction: identical bytes hash to one ``blob_id``, so
        the same document submitted twice yields one stored copy and one identity
        (FR-021).
        """
        from docdoc.ingest.source import SourceFile, detect_media_type

        deployment = _deployment_of(request)
        if not deployment.has_store:
            # Accepting bytes we cannot keep, and handing back an identity that
            # will never resolve, is the worse answer (FR-068).
            raise _no_store_configured()

        data = await _read_capped(request, deployment.max_request_bytes)

        # From the bytes, never from a client-declared type. A `Content-Type`
        # header is an assertion by the sender and this is a check.
        media_type = detect_media_type(data)
        file = SourceFile.from_bytes(data, limits=deployment.limits)
        file.check_limits(deployment.limits or _default_limits())

        assert deployment.blobs is not None
        blob_id = deployment.blobs.put(data)
        return SubmissionResponse(
            blob_id=blob_id,
            size_bytes=len(data),
            media_type=media_type or file.media_type,
        )

    @router.get("/documents/{blob_id}", response_model=BlobMetadata)
    async def document(request: Request, blob_id: str) -> Any:
        """Identity, size, and detected media type. Never the bytes."""
        from docdoc.ingest.source import detect_media_type

        deployment = _deployment_of(request)
        if not deployment.has_store:
            raise _no_store_configured()

        assert deployment.blobs is not None
        size = deployment.blobs.size_of(blob_id)
        if size is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"class": "UnknownBlob", "message": "no such document here"}},
            )

        data = deployment.blobs.get(blob_id)
        return BlobMetadata(
            blob_id=blob_id,
            size_bytes=size,
            media_type=None if data is None else detect_media_type(data),
        )

    @router.post("/documents/{blob_id}/extract")
    async def extract(request: Request, blob_id: str, schema: str) -> Any:
        """Run the pipeline inside the request and return the id **and** result."""
        from docdoc.pipeline import run as run_pipeline

        deployment = _deployment_of(request)
        if not deployment.has_store:
            raise _no_store_configured()

        assert deployment.blobs is not None
        data = deployment.blobs.get(blob_id)
        if data is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"class": "UnknownBlob", "message": "no such document here"}},
            )

        result = run_pipeline(
            data,
            schema=schema,
            registry=deployment.registry(),
            adapter=deployment.adapter(),
            store=deployment.artifact_store(),
            limits=deployment.limits,
            request_id=request.headers.get("x-request-id"),
        )

        if result.failed_stage is not None or result.processing_id is None:
            # No terminal artifact, so no job — and therefore this response is
            # the only place the completed stages' results can appear (FR-066).
            return JSONResponse(
                status_code=api_errors.status_for_failed_run(result),
                content=api_errors.body_for_failed_run(result).model_dump(mode="json"),
            )

        return _run_response(result)

    @router.get("/jobs/{job_id}", response_model=JobStatusResponse)
    async def job(request: Request, job_id: str) -> Any:
        """One of three statuses, and never ``pending`` (FR-035)."""
        deployment = _deployment_of(request)

        if not _well_formed(job_id):
            return JobStatusResponse(
                job_id=job_id,
                status=JobStatus.UNKNOWN,
                detail="not a well-formed artifact identity, so no run produced it",
            )

        if _terminal(deployment, job_id) is None:
            return JobStatusResponse(
                job_id=job_id,
                status=JobStatus.UNAVAILABLE,
                detail=(
                    "not in this store. It was produced elsewhere, produced with no "
                    "store, or cleared — an append-only store keeps no record of "
                    "which"
                ),
            )

        return JobStatusResponse(job_id=job_id, status=JobStatus.SUCCEEDED)

    @router.get("/jobs/{job_id}/result")
    async def job_result(request: Request, job_id: str) -> Any:
        """The stored result, and never a silent recomputation (FR-036)."""
        deployment = _deployment_of(request)

        if not _well_formed(job_id):
            return _status_only(job_id, JobStatus.UNKNOWN)

        validation = _terminal(deployment, job_id)
        if validation is None:
            # Deliberately not recomputed: the inputs may have moved since, and
            # returning a different result under the same id would break the one
            # promise the identity makes.
            return _status_only(job_id, JobStatus.UNAVAILABLE)

        return _stored_result(deployment, job_id, validation)

    return router


def _run_response(result: PipelineResult) -> RunResponse:
    """A completed run, serialised. The interface produces no different one."""
    assert result.processing_id is not None
    return RunResponse(
        job_id=result.processing_id,
        document_id=None if result.document is None else result.document.id,
        schema_identity=result.provenance.schema_identity,
        verdict=None if result.validation is None else result.validation.verdict.value,
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
        extraction=None if result.extraction is None else result.extraction.model_dump(mode="json"),
        grounding=None if result.grounding is None else result.grounding.model_dump(mode="json"),
        validation=None if result.validation is None else result.validation.model_dump(mode="json"),
    )


def _terminal(deployment: _Deployment, job_id: str) -> Any:
    """The validation artifact this job id addresses, or ``None``."""
    from docdoc.artifacts import ArtifactError
    from docdoc.pipeline.stages import Stage, spec_for
    from docdoc.validation.result import ValidationResult

    if deployment.store is None:
        return None
    try:
        return deployment.store.get(
            job_id,
            model=ValidationResult,
            artifact_format_version=spec_for(Stage.VALIDATE).artifact_format_version,
        )
    except ArtifactError:
        # A corrupted artifact is not "unavailable" — it is a fault, and the
        # error handler turns it into a 500 naming the store. Recomputing over it
        # would hide a failing disk behind a slower response.
        raise


def _stored_result(deployment: _Deployment, job_id: str, validation: Any) -> Any:
    """Rebuild a run's response from the store, walking the chain back.

    Each stage's artifact records the identity of its input, so the whole run is
    reachable from the terminal id alone. That is the property FR-022 asks this
    milestone to guarantee for a future collector, and it pays for itself here.
    """
    from docdoc.extraction.extract import ExtractionResult
    from docdoc.grounding.result import GroundingResult
    from docdoc.pipeline.stages import Stage, spec_for

    provenance = validation.provenance
    store = deployment.store

    def _load(artifact_id: str, model: Any, stage: Stage) -> Any:
        if store is None:
            return None
        return store.get(
            artifact_id,
            model=model,
            artifact_format_version=spec_for(stage).artifact_format_version,
        )

    grounding = _load(provenance.grounding_artifact_id, GroundingResult, Stage.GROUND)
    extraction = _load(provenance.extraction_artifact_id, ExtractionResult, Stage.EXTRACT)

    return RunResponse(
        job_id=job_id,
        document_id=provenance.document_id,
        schema_identity=provenance.schema_identity,
        verdict=validation.verdict.value,
        # No outcomes: this is a *retrieval*, not a run, and reporting stage
        # statuses for work this request did not do would be fiction. The run
        # that produced them reported them in its own response.
        outcomes=(),
        extraction=None if extraction is None else extraction.model_dump(mode="json"),
        grounding=None if grounding is None else grounding.model_dump(mode="json"),
        validation=validation.model_dump(mode="json"),
    )


def _status_only(job_id: str, status: JobStatus) -> JSONResponse:
    return JSONResponse(
        status_code=404,
        content=JobStatusResponse(
            job_id=job_id,
            status=status,
            detail=(
                "not a well-formed artifact identity"
                if status is JobStatus.UNKNOWN
                else "no result is stored under this identity, and it is not recomputed"
            ),
        ).model_dump(mode="json"),
    )


def _well_formed(job_id: str) -> bool:
    """Whether this could be an artifact identity at all.

    A syntactic judgement, which is exactly what makes ``unknown`` answerable
    without history — see ``JobStatus``. The same shape ``FileArtifactStore``
    already refuses, checked here so the refusal is a status rather than a 500.
    """
    prefix, _, digest = job_id.partition(":")
    return (
        prefix == "sha256"
        and len(digest) == 64
        and all(character in "0123456789abcdef" for character in digest)
    )


def _default_limits() -> Any:
    from docdoc.ingest.source import Limits

    return Limits()


def _no_store_configured() -> Exception:
    from docdoc.artifacts import ArtifactError

    return ArtifactError(
        "no artifact store is configured, so this deployment cannot keep a "
        f"submitted document or answer for a job. Set ${STORE_ROOT_ENV}.",
        reason="not_configured",
    )


async def _read_capped(request: Request, cap: int) -> bytes:
    """Read the body, refusing once it exceeds the cap.

    Streamed and counted rather than awaited whole: ``await request.body()``
    would buffer the entire upload before anything could object, which makes the
    limit a report of what already happened. FR-039 wants the refusal to come
    *before* the cost, and for a request body the only moment before the cost is
    while it is arriving (research R10).
    """
    from docdoc.ingest.errors import UnsupportedDocumentError

    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > cap:
            raise UnsupportedDocumentError(
                f"request body exceeds the configured maximum of {cap} bytes",
                reason="size",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _install_error_handler(app: FastAPI) -> None:
    """One boundary, so that no untyped exception reaches a caller (FR-051)."""
    from docdoc.kernel.errors import DocdocError

    @app.exception_handler(DocdocError)
    async def _typed(request: Request, error: DocdocError) -> Response:
        return JSONResponse(
            status_code=api_errors.status_for(error),
            content=api_errors.body_for_exception(error, stage=_stage_of(error)).model_dump(
                mode="json"
            ),
        )

    @app.exception_handler(Exception)
    async def _untyped(request: Request, error: Exception) -> Response:
        # An untyped exception is docdoc's fault by definition — every expected
        # failure in this system has a type. It becomes a 500 naming the class
        # and nothing else: an unexpected exception's message is the one least
        # likely to have been written with a document's contents in mind.
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "class": type(error).__name__,
                    "stage": None,
                    "message": "an unexpected error occurred",
                    "detail": {},
                }
            },
        )


def _stage_of(error: BaseException) -> str | None:
    """Which layer declared this error, read off its own module (FR-005)."""
    module = type(error).__module__
    for marker, stage in (
        (".ingest", "parse"),
        (".extraction", "extract"),
        (".grounding", "ground"),
        (".validation", "validate"),
        (".artifacts", "store"),
        (".pipeline", "pipeline"),
    ):
        if marker in module:
            return stage
    return None
