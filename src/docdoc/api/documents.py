"""`/v1/documents*`, `/v1/extract`, `/v1/schemas`, and `/v1/jobs*`."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from docdoc.api import errors as api_errors
from docdoc.api.deps import (
    Caller,
    _default_limits,
    _deployment_of,
    _no_store_configured,
    _read_capped,
    _stores_of,
)
from docdoc.api.models import (
    BlobMetadata,
    JobStatus,
    JobStatusResponse,
    RunResponse,
    SchemaChoice,
    SchemaListing,
    StageOutcomeView,
    StorelessRunResponse,
    SubmissionResponse,
)

if TYPE_CHECKING:
    from docdoc.pipeline import PipelineResult


def routes() -> APIRouter:
    """`/documents*` and `/extract`, registered before the OCR and run routes."""
    router = APIRouter()

    @router.post("/documents", response_model=SubmissionResponse)
    async def submit(request: Request, principal: Caller) -> Any:
        """Store source bytes and return their identity.

        Idempotent by construction: identical bytes hash to one ``blob_id``, so
        the same document submitted twice yields one stored copy and one identity
        (FR-021).

        The bytes land in **this tenant's** namespace. Two tenants submitting the
        same document derive the same ``blob_id`` and store two copies, which is
        ADR-0014 §4's forfeited cross-tenant reuse being paid for here, in the
        one place it is visible.
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

        _, blobs = _stores_of(request, principal)
        assert blobs is not None
        blob_id = blobs.put(data)
        return SubmissionResponse(
            blob_id=blob_id,
            size_bytes=len(data),
            media_type=media_type or file.media_type,
        )

    @router.get("/documents/{blob_id}", response_model=BlobMetadata)
    async def document(request: Request, blob_id: str, principal: Caller) -> Any:
        """Identity, size, and detected media type. Never the bytes.

        Scoped to the caller's tenant (FR-064). Another tenant's ``blob_id`` is
        simply not in this namespace, so it produces the same 404 as one that was
        never submitted — the same body, from the same branch, because there is
        no second branch to give a different one (FR-066).
        """
        from docdoc.ingest.source import detect_media_type

        deployment = _deployment_of(request)
        if not deployment.has_store:
            raise _no_store_configured()

        _, blobs = _stores_of(request, principal)
        assert blobs is not None
        size = blobs.size_of(blob_id)
        if size is None:
            return JSONResponse(
                status_code=404,
                content={"error": {"class": "UnknownBlob", "message": "no such document here"}},
            )

        data = blobs.get(blob_id)
        return BlobMetadata(
            blob_id=blob_id,
            size_bytes=size,
            media_type=None if data is None else detect_media_type(data),
        )

    # A plain `def`, not `async def`: there is no body to `await`, and FastAPI
    # runs a synchronous handler in its worker thread pool. The store reads, the
    # parse, the provider call and the writes all block, and on the event loop
    # one slow document would stall every other request on this process,
    # health probes included (#18).
    @router.post("/documents/{blob_id}/extract")
    def extract(
        request: Request,
        blob_id: str,
        schema: str,
        principal: Caller,
    ) -> Any:
        """Run the pipeline inside the request and return the id **and** result.

        Reads and writes this tenant's namespace throughout, so reuse operates
        strictly within a tenant (FR-086) and a document another tenant has
        already processed costs this one exactly as much as a first-ever
        submission — which is SC-017, the half of isolation a status code cannot
        deliver.
        """
        from docdoc.pipeline import run as run_pipeline

        deployment = _deployment_of(request)
        if not deployment.has_store:
            raise _no_store_configured()

        store, blobs = _stores_of(request, principal)
        assert blobs is not None
        data = blobs.get(blob_id)
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
            store=_artifact_store(store),
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

    @router.post("/extract")
    async def extract_storeless(request: Request, schema: str) -> Any:
        """Run the pipeline over submitted bytes and persist nothing.

        The body is the document; ``schema`` is a concrete ``name@version``.

        **``NullArtifactStore()`` unconditionally**, and that word is the
        requirement (FR-008). Reaching for ``deployment.artifact_store()`` here
        would make persistence a property of how the deployment happens to be
        configured, when it is a property of the endpoint the caller chose. A
        deployment *with* a store gets the same nothing written as one without.
        """
        from fastapi.concurrency import run_in_threadpool

        from docdoc.artifacts import NullArtifactStore
        from docdoc.ingest.source import SourceFile
        from docdoc.pipeline import run as run_pipeline

        deployment = _deployment_of(request)

        # No `has_store` check: this route is the one that needs none, which is
        # its entire reason for existing.
        # The body still has to be read with `await` -- `_read_capped` drives
        # `request.stream()`, an async generator.
        data = await _read_capped(request, deployment.max_request_bytes)

        # Everything below blocks -- the parse, the provider call -- and on the
        # loop it would stall every other request, health probes included (#18).
        def _run() -> Any:
            # The submission path's limits, reused rather than restated, so FR-005
            # holds by calling the same code. From the bytes, never from a
            # client-declared `Content-Type`.
            file = SourceFile.from_bytes(data, limits=deployment.limits)
            file.check_limits(deployment.limits or _default_limits())

            result = run_pipeline(
                data,
                schema=schema,
                registry=deployment.registry(),
                adapter=deployment.adapter(),
                store=NullArtifactStore(),
                limits=deployment.limits,
                request_id=request.headers.get("x-request-id"),
            )

            if result.failed_stage is not None:
                # Same body, same statuses, same partial results as the store-backed
                # route (FR-006). A storeless run fails identically or the two paths
                # are not the same pipeline.
                return JSONResponse(
                    status_code=api_errors.status_for_failed_run(result),
                    content=api_errors.body_for_failed_run(result).model_dump(mode="json"),
                )

            return _storeless_run_response(result)

        return await run_in_threadpool(_run)

    return router


def jobs_routes() -> APIRouter:
    """`/schemas` and `/jobs*`, registered after the run routes."""
    router = APIRouter()

    @router.get("/schemas", response_model=SchemaListing)
    async def schemas(request: Request) -> Any:
        """The identities this deployment has configured, sorted.

        A projection of ``SchemaRegistry.identities()``, which already returns
        exactly the set ``resolve()`` accepts — so there is no translation here
        to get wrong, and a listed identity is runnable verbatim (FR-010).
        """
        deployment = _deployment_of(request)
        registry = deployment.registry()
        return SchemaListing(
            schemas=tuple(SchemaChoice(identity=identity) for identity in registry.identities())
        )

    @router.get("/jobs/{job_id}", response_model=JobStatusResponse)
    async def job(request: Request, job_id: str, principal: Caller) -> Any:
        """One of three statuses, and never ``pending`` (FR-035).

        Read from the caller's own namespace, which is what makes FR-065 true:
        another tenant's ``processing_id`` is not in this store, so it answers
        ``unavailable`` — the identical body an identity nobody produced gets.
        """
        store, _ = _stores_of(request, principal)

        if not _well_formed(job_id):
            return JobStatusResponse(
                job_id=job_id,
                status=JobStatus.UNKNOWN,
                detail="not a well-formed artifact identity, so no run produced it",
            )

        if _terminal(store, job_id) is None:
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
    async def job_result(request: Request, job_id: str, principal: Caller) -> Any:
        """The stored result, and never a silent recomputation (FR-036)."""
        store, _ = _stores_of(request, principal)

        if not _well_formed(job_id):
            return _status_only(job_id, JobStatus.UNKNOWN)

        validation = _terminal(store, job_id)
        if validation is None:
            # Deliberately not recomputed: the inputs may have moved since, and
            # returning a different result under the same id would break the one
            # promise the identity makes.
            return _status_only(job_id, JobStatus.UNAVAILABLE)

        return _stored_result(store, job_id, validation)

    return router


def _run_fields(result: PipelineResult) -> dict[str, Any]:
    """Everything a completed run reports except its identity.

    Shared by both run responses so that the storeless one differs from the
    store-backed one in exactly the field it is defined to omit, and in nothing
    that drifted. SC-006 asserts the two agree; this is what makes agreeing the
    default rather than something to maintain.
    """
    return {
        "document_id": None if result.document is None else result.document.id,
        "schema_identity": result.provenance.schema_identity,
        "verdict": None if result.validation is None else result.validation.verdict.value,
        "outcomes": tuple(
            StageOutcomeView(
                stage=outcome.stage.value,
                status=outcome.status.value,
                artifact_id=outcome.artifact_id,
                duration_ms=outcome.duration_ms,
                failure_class=outcome.failure_class,
            )
            for outcome in result.outcomes
        ),
        "extraction": None
        if result.extraction is None
        else result.extraction.model_dump(mode="json"),
        "grounding": None if result.grounding is None else result.grounding.model_dump(mode="json"),
        "validation": None
        if result.validation is None
        else result.validation.model_dump(mode="json"),
    }


def _run_response(result: PipelineResult) -> RunResponse:
    """A completed run, serialised. The interface produces no different one."""
    assert result.processing_id is not None
    return RunResponse(job_id=result.processing_id, **_run_fields(result))


def _storeless_run_response(result: PipelineResult) -> StorelessRunResponse:
    """A completed run that wrote nothing, and therefore has no job (FR-003).

    No ``processing_id`` assertion, and no ``processing_id`` field: with a null
    store no terminal artifact exists, so the id ADR-0003 defines as the job id
    was never produced. That is the shape of the choice, not a gap in it.
    """
    return StorelessRunResponse(**_run_fields(result))


def _terminal(store: Any, job_id: str) -> Any:
    """The validation artifact this job id addresses, or ``None``.

    Takes the store rather than the deployment, because *which* store is now a
    property of the request: it is the caller's tenant's. A helper that reached
    for `deployment.store` would read the default tenant's namespace for every
    caller, which is the cross-tenant read FR-065 forbids — and it would do so
    silently, returning correct-looking results.
    """
    from docdoc.artifacts import ArtifactError
    from docdoc.pipeline.stages import Stage, spec_for
    from docdoc.validation.result import ValidationResult

    if store is None:
        return None
    try:
        # **Only a validation artifact is a job** (ADR-0022). An OCR run's
        # `processing_id` is a parse artifact in the same store, and reading it
        # as a `ValidationResult` is a model mismatch the store reports as
        # corruption -- a 500 on the jobs routes and on any run read a routing
        # policy decorates. Asked about the stage first, it is simply not a job.
        envelope = store.envelope(job_id)
        if envelope is None or envelope.stage != Stage.VALIDATE.value:
            return None
        return store.get(
            job_id,
            model=ValidationResult,
            artifact_format_version=spec_for(Stage.VALIDATE).artifact_format_version,
        )
    except ArtifactError:
        # A corrupted artifact is not "unavailable" — it is a fault, and the
        # error handler turns it into a 500 naming the store. Recomputing over it
        # would hide a failing disk behind a slower response.
        raise


def _stored_result(store: Any, job_id: str, validation: Any) -> Any:
    """Rebuild a run's response from the store, walking the chain back.

    Each stage's artifact records the identity of its input, so the whole run is
    reachable from the terminal id alone. That is the property FR-022 asks this
    milestone to guarantee for a future collector, and it pays for itself here.
    """
    from docdoc.extraction.extract import ExtractionResult
    from docdoc.grounding.result import GroundingResult
    from docdoc.pipeline.stages import Stage, spec_for

    provenance = validation.provenance

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


def _artifact_store(store: Any) -> Any:
    """A store, or the one that stores nothing.

    The pipeline's `store=` may not be `None`, and a deployment with no store
    configured is an ordinary deployment rather than a broken one: every stage
    runs every time and nothing is written (FR-017).
    """
    from docdoc.artifacts import NullArtifactStore

    return store or NullArtifactStore()
