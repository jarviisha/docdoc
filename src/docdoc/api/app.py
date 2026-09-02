"""Seven endpoints, one synchronous run each, and no state of its own.

The run happens inside the request. There is no queue, no worker pool, no
background executor, and no job table — not as a simplification of an
asynchronous design, but because the identity model does not permit one. A job id
that *is* the terminal artifact id cannot be issued before the run, since that id
is not knowable until the stages feeding it have finished (research R7). Running
inside the request dissolves the problem: by the time there is something to hand
back, the id exists.

**A store is a deployment decision, and four endpoints need one** (FR-068).
Submission has nowhere to put bytes without it, and a job lookup is definitionally
a store lookup. ``POST /v1/documents/{blob_id}/extract`` needs one too, and not
incidentally: its input is a ``blob_id``, a ``blob_id`` exists only after a
submission, and submission is refused without a store.

That coupling meant there was no way, over HTTP, to run an extraction without the
document first coming to rest on disk — an objection this project had already
accepted elsewhere, when the ``gcv`` adapter declined Vision's asynchronous API
for requiring "a place for document content to come to rest outside the process".
``POST /v1/extract`` is the path that needs no store (Milestone 8 FR-001,
ADR-0012). It is also the reason this docstring no longer claims that running an
extraction needs none: for five endpoints that sentence was simply false.

**Limits are enforced in two places, and both are necessary.** The request body
cap is applied while reading, before the body is buffered — the one limit
``ingest.Limits`` cannot know about, because by the time bytes reach it they are
already in memory (research R10). Document size and the media-type allowlist are
``ingest.Limits``'s, reused rather than restated, and are checked from the bytes
and never from a client-declared type.
"""

from __future__ import annotations

import json
import logging
import os
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from docdoc.api import errors as api_errors
from docdoc.api.models import (
    BlobMetadata,
    JobStatus,
    JobStatusResponse,
    RunAcceptedResponse,
    RunResponse,
    RunStateResponse,
    SchemaChoice,
    SchemaListing,
    StageOutcomeView,
    StorelessRunResponse,
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
        runs: Any = None,
    ) -> None:
        self.store = store
        self.blobs = blobs
        self._runs = runs
        self._registry = registry
        self._adapter = adapter
        self.limits = limits
        self.max_request_bytes = max_request_bytes or _configured_request_cap()

    @property
    def has_store(self) -> bool:
        return self.blobs is not None

    @property
    def has_runs(self) -> bool:
        """Whether this deployment can accept asynchronous runs.

        False is a valid configuration and not a degraded one: a deployment using
        only the synchronous routes needs no database, and Milestone 8's install
        keeps working untouched.
        """
        return self._runs is not None

    def runs(self) -> Any:
        if self._runs is None:
            raise RuntimeError("no run store configured")
        return self._runs

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

    runs = _configured_run_queue()

    root = os.environ.get(STORE_ROOT_ENV, "").strip()
    if not root:
        return _Deployment(runs=runs)
    return _Deployment(store=FileArtifactStore(root), blobs=BlobStore(root), runs=runs)


def _configured_run_queue() -> Any:
    """The run store, or ``None`` when this deployment accepts no asynchronous runs.

    ``None`` is a supported configuration and not a degraded one. A Milestone 8
    install upgrades with no change and keeps working: it uses the synchronous
    routes, which need no database, and the run routes answer 503 with a sentence
    naming what to configure rather than failing at import.

    Nothing is migrated here. ``docdoc migrate`` is explicit precisely so that
    several workers booting at once are not several processes altering one table
    (FR-078), and an API that quietly applied a schema would defeat that from the
    other side.
    """
    dsn = os.environ.get("DOCDOC_RUN_DATABASE_URL", "").strip()
    if not dsn:
        return None

    try:
        import psycopg
    except ImportError:
        # Configured but unusable. Left as `None` so the route answers 503 with
        # its own message; raising here would take the whole service down for a
        # capability the synchronous routes do not need.
        logging.getLogger("docdoc.api").warning(
            json.dumps({"event": "runs.driver_missing", "extra": "docdoc[postgres]"})
        )
        return None

    from docdoc.runs.postgres import PostgresRunQueue

    return PostgresRunQueue(lambda: psycopg.connect(dsn))


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
    _mount_ui(app)
    return app


def _mount_ui(app: FastAPI) -> None:
    """Serve the browser client from this origin, or explain its absence.

    **Same origin, so no cross-origin configuration exists anywhere** (Milestone 8
    FR-034). That is the whole reason the assets are mounted here rather than
    served by something else: a second origin would need a CORS policy, and a
    CORS policy is a thing to get wrong.

    Mounted under ``/ui`` and never at the root, so that adding an interface
    cannot shadow an API path — now or when a later route is added by someone who
    has forgotten this exists.
    """
    from docdoc.api.ui import absence_reason, chosen_assets

    source, assets = chosen_assets()

    if assets is None:
        # Not an error: a deployment without the `ui` extra is a supported and
        # ordinary deployment. But a blank page is exactly what FR-037 forbids,
        # so the one route that exists says what is missing and what fixes it.
        @app.get("/ui", include_in_schema=False)
        @app.get("/ui/{path:path}", include_in_schema=False)
        async def _no_ui(path: str = "") -> JSONResponse:
            return JSONResponse(
                status_code=501,
                content={
                    "error": {
                        "class": "ViewerNotInstalled",
                        "message": absence_reason(),
                    }
                },
            )

        return

    from fastapi.staticfiles import StaticFiles

    # **Say which of the three roots won.** Three places can hold three different
    # builds, and until this line nothing named the winner: a stale installed
    # distribution shadowed a fresh `ui/dist`, every rebuild appeared to do
    # nothing, and the months-old page that resulted was read as evidence about
    # current code.
    #
    # **Visible only where the application configures logging**, which uvicorn's
    # defaults do not — it sets up `uvicorn.*` and leaves root without a handler,
    # so this INFO falls to `logging.lastResort` and is dropped. That is how every
    # structured event docdoc emits behaves, and adding a handler here would be
    # the library deciding for the application. `docdoc.api.ui.chosen_assets` is
    # the answer that needs no logging at all, and it is what the documentation
    # tells a developer to run.
    #
    # Not a second request-logging path, and so not the thing T019 forbids: it
    # runs once at construction, and it carries a filesystem path and no document
    # content, no values and no credentials (FR-033).
    logging.getLogger("docdoc.api").info(
        json.dumps({"event": "ui.assets", "source": source, "path": str(assets)})
    )

    app.mount("/ui", StaticFiles(directory=assets, html=True), name="ui")


def create_app() -> FastAPI:
    """The ASGI factory, for ``uvicorn docdoc.api.app:create_app --factory``."""
    return build_app()


class _RunSpec:
    """What a submission carries before it is a run.

    A plain object rather than a pydantic model: `RunQueue.submit` takes a
    structural `RunSpec`, and building a validated model here would validate the
    same five strings twice — once at the HTTP boundary and once on the way to a
    database that has its own constraints.
    """

    __slots__ = ("blob_id", "idempotency_key", "request_id", "schema_identity", "tenant_id")

    def __init__(
        self,
        *,
        tenant_id: str,
        blob_id: str,
        schema_identity: str,
        request_id: str | None,
        idempotency_key: str | None,
    ) -> None:
        self.tenant_id = tenant_id
        self.blob_id = blob_id
        self.schema_identity = schema_identity
        self.request_id = request_id
        self.idempotency_key = idempotency_key


def _unknown_run(run_id: str) -> JSONResponse:
    """The one answer for unknown, malformed, and another tenant's (FR-066).

    One function so the three cannot drift apart into three messages, which is
    how an existence oracle gets written by accident.
    """
    return JSONResponse(
        status_code=404,
        content={"error": {"class": "RunNotFoundError", "message": "no such run here"}},
    )


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
        from docdoc.artifacts import NullArtifactStore
        from docdoc.ingest.source import SourceFile
        from docdoc.pipeline import run as run_pipeline

        deployment = _deployment_of(request)

        # No `has_store` check: this route is the one that needs none, which is
        # its entire reason for existing.
        data = await _read_capped(request, deployment.max_request_bytes)

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

    @router.post("/documents/{blob_id}/runs", status_code=202)
    async def submit_run(request: Request, blob_id: str, schema: str) -> Any:
        """Accept a run and return before any stage executes.

        The response carries a ``run_id`` and **omits** ``processing_id``, which
        does not exist yet and cannot: it is the terminal artifact id, derived
        from stage outputs (ADR-0013 §1). That is the whole reason this is a new
        resource rather than a ``pending`` status on ``GET /v1/jobs``.
        """
        from docdoc.artifacts import ArtifactError
        from docdoc.runs.errors import RunStateUnavailableError
        from docdoc.runs.identity import DEFAULT_RETENTION, deadline, new_run_id, now
        from docdoc.runs.model import DEFAULT_TENANT

        deployment = _deployment_of(request)
        if not deployment.has_runs:
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "class": "RunStateUnavailableError",
                        "message": (
                            "this deployment accepts no asynchronous runs; set "
                            "DOCDOC_RUN_DATABASE_URL and apply `docdoc migrate`"
                        ),
                    }
                },
            )
        if not deployment.has_store:
            raise _no_store_configured()

        tenant = DEFAULT_TENANT
        assert deployment.blobs is not None
        # A malformed identity and an absent one get the same 404. The store
        # raises `ArtifactError` for the first, which would surface as a 500 —
        # and "the server broke" is the wrong thing to tell a caller who typed a
        # bad identifier. The two synchronous routes still answer 500 here; that
        # is pre-existing and FR-009 requires their status codes to be unchanged,
        # so it is reported rather than fixed under this milestone.
        try:
            known = deployment.blobs.get(blob_id) is not None
        except ArtifactError:
            known = False
        if not known:
            return JSONResponse(
                status_code=404,
                content={"error": {"class": "UnknownBlob", "message": "no such document here"}},
            )

        if schema not in deployment.registry().identities():
            return JSONResponse(
                status_code=422,
                content={
                    "error": {"class": "SchemaError", "message": "schema is not configured here"}
                },
            )

        spec = _RunSpec(
            tenant_id=tenant,
            blob_id=blob_id,
            schema_identity=schema,
            request_id=request.headers.get("x-request-id"),
            idempotency_key=request.headers.get("idempotency-key"),
        )
        started = now()
        try:
            run = deployment.runs().submit(
                spec,
                run_id=new_run_id(),
                now=started,
                expires_at=deadline(started, DEFAULT_RETENTION),
            )
        except RunStateUnavailableError as exc:
            # Refused rather than accepted and dropped (FR-057). A run that
            # cannot be recorded is work that will never be done and never be
            # reported, which is the silent failure this status exists to avoid.
            return JSONResponse(
                status_code=503,
                content={"error": {"class": type(exc).__name__, "message": str(exc)}},
            )

        return JSONResponse(
            status_code=202,
            content=RunAcceptedResponse(
                run_id=str(run.run_id),
                status=str(run.status),
                created_at=run.created_at.isoformat(),
            ).model_dump(mode="json"),
        )

    @router.get("/runs/{run_id}", response_model=RunStateResponse)
    async def run_state(request: Request, run_id: str) -> Any:
        """One of the five states, and never the result itself.

        A succeeded run names its ``processing_id``; the unchanged
        ``GET /v1/jobs/{processing_id}/result`` serves the result. One result
        representation, reachable one way (FR-013).
        """
        from uuid import UUID

        deployment = _deployment_of(request)
        if not deployment.has_runs:
            return _unknown_run(run_id)

        from docdoc.runs.model import DEFAULT_TENANT

        try:
            identity = UUID(run_id)
        except ValueError:
            # A malformed identifier and an unknown one get the same answer: a
            # different one would tell a caller which identifiers are well-formed
            # enough to exist (FR-066).
            return _unknown_run(run_id)

        run = deployment.runs().get(identity, DEFAULT_TENANT)
        if run is None:
            return _unknown_run(run_id)

        return RunStateResponse(**run.dump_public())

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
