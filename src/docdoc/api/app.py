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
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from docdoc.api import errors as api_errors
from docdoc.api import health
from docdoc.api.auth import AuthenticationError, KeyRing, Principal, bearer_of
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
    RUN_DATABASE_URL_ENV,
    SCHEMA_PATHS_ENV,
    STORE_ROOT_ENV,
    STORE_URL_ENV,
)
from docdoc.runs.health import LIVENESS_PATH, READINESS_PATH
from docdoc.runs.model import DEFAULT_TENANT

if TYPE_CHECKING:
    from docdoc.artifacts import ArtifactStore, BlobStore
    from docdoc.pipeline import PipelineResult

__all__ = ["build_app", "create_app"]

#: How long a connection attempt to the run-state database waits, in seconds.
#: Not configurable: it bounds a probe and a request that a caller is already
#: waiting on, and a deployment that wanted a longer one would be asking for a
#: slower failure rather than a different outcome.
CONNECT_TIMEOUT_SECONDS = 5

#: FR-059's whole exemption list. Two paths, because a probe cannot carry a
#: credential — kubelet, an ELB target group and Docker's `HEALTHCHECK` all issue
#: a bare request, so requiring one here would make every authenticated
#: deployment permanently unhealthy.
#:
#: Imported rather than spelled again: these are the same two strings the routes
#: are registered under, and two copies of a path is how one of them ends up
#: exempting something that no longer exists.
_UNAUTHENTICATED = frozenset({LIVENESS_PATH, READINESS_PATH})

#: The run layer's errors, mapped. Kept beside `api_errors.STATUS_BY_ERROR`
#: rather than inside it, because that table is the constitution's error model
#: and these are not part of it — see the handler's docstring.
_RUN_ERROR_STATUS = {
    "RunNotFoundError": 404,
    "RunNotCancellableError": 409,
    "RunStateUnavailableError": 503,
}


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
        store_root: Any = None,
        store_url: str | None = None,
        registry: Any = None,
        adapter: Any = None,
        max_request_bytes: int | None = None,
        limits: Any = None,
        runs: Any = None,
        keys: Any = None,
    ) -> None:
        self._store_root = store_root
        self._store_url = store_url
        self._by_tenant: dict[str, tuple[Any, Any]] = {}

        if store is None and blobs is None and (store_root or store_url):
            store, blobs = self._build_stores(DEFAULT_TENANT)
        self.store = store
        self.blobs = blobs
        if store is not None or blobs is not None:
            self._by_tenant[DEFAULT_TENANT] = (store, blobs)

        self._runs = runs
        self._readiness: Any = None
        self._registry = registry
        self._adapter = adapter
        self.limits = limits
        self.keys = keys if keys is not None else KeyRing.from_environment()
        self.max_request_bytes = max_request_bytes or _configured_request_cap()

    @property
    def has_store(self) -> bool:
        return self.blobs is not None

    @property
    def can_namespace(self) -> bool:
        """Whether this deployment can build a store for a tenant it has not seen.

        False when it was handed store *instances* rather than a location. That
        is fine with authentication off — there is one tenant and those instances
        are its stores — and it is a refusal to start with authentication on, see
        ``build_app``.
        """
        return bool(self._store_root or self._store_url)

    def stores_for(self, tenant_id: str) -> tuple[Any, Any]:
        """``(artifact_store, blob_store)`` namespaced to one tenant (FR-084).

        The namespacing is in the *path*, not in a check after the read: a store
        built for tenant A cannot see tenant B's objects, so there is no moment
        where one tenant's content exists in memory next to a decision about
        whether it should. That is what makes FR-064 and FR-065 true by
        construction rather than by every call site remembering a comparison.

        Built once per tenant and cached. A store is a client and a path prefix,
        so rebuilding one per request would open a connection pool per request on
        the object-store path.
        """
        cached = self._by_tenant.get(tenant_id)
        if cached is not None:
            return cached
        built = self._build_stores(tenant_id)
        self._by_tenant[tenant_id] = built
        return built

    def _build_stores(self, tenant_id: str) -> tuple[Any, Any]:
        from docdoc.artifacts import BlobStore, FileArtifactStore
        from docdoc.artifacts.s3 import stores_from_url

        if self._store_url:
            return stores_from_url(self._store_url, tenant_id=tenant_id)
        if self._store_root:
            return (
                FileArtifactStore(self._store_root, tenant_id=tenant_id),
                BlobStore(self._store_root, tenant_id=tenant_id),
            )
        # No location, so nothing can be built for a tenant this deployment was
        # not handed a store for. Returning the default tenant's stores here
        # would be a cross-tenant leak written as a convenience.
        return (None, None)

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

    def readiness(self) -> Any:
        """The readiness probe over this deployment's actual dependencies.

        Built once and held, so that the two-second cache is shared across
        requests rather than being one cache per probe — an uncached check makes
        probe traffic scale with fleet size against the component already under
        stress (research R13).

        A deployment with neither a run-state database nor a store has nothing to
        probe and is **ready**. That is a Milestone 8 install: validly
        configured, and it must not become permanently unready on upgrade
        (SC-018).
        """
        from docdoc.runs.health import Readiness

        if self._readiness is None:
            self._readiness = Readiness(runs=self._runs, blobs=self.blobs)
        return self._readiness

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

    The *location* is held rather than a pair of store objects, because a
    multi-tenant deployment needs one store per tenant and only a location can
    produce one. The default tenant's stores are built from it immediately, and
    they land exactly where a Milestone 8 deployment already wrote — unprefixed
    (FR-084a).
    """
    runs = _configured_run_queue()

    # An object store, if one was named. Checked first because a deployment that
    # sets both meant the more specific one, and because `DOCDOC_STORE_ROOT`
    # keeps working untouched for everyone who sets only it (SC-018).
    url = os.environ.get(STORE_URL_ENV, "").strip()
    if url:
        return _Deployment(store_url=url, runs=runs)

    root = os.environ.get(STORE_ROOT_ENV, "").strip()
    if not root:
        return _Deployment(runs=runs)
    return _Deployment(store_root=root, runs=runs)


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
    dsn = os.environ.get(RUN_DATABASE_URL_ENV, "").strip()
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

    # A short connect timeout, because both things that reach this connection
    # have a deadline somebody else set: a readiness probe is polled on an
    # interval and a submission is a request a caller is waiting on. libpq's
    # default is to wait indefinitely, which turns "the database is down" into
    # "the probe never answers" — and an unanswered probe is read as a hung
    # process rather than as an unmet dependency (research R13).
    return PostgresRunQueue(lambda: psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS))


def build_app(deployment: _Deployment | None = None) -> FastAPI:
    """The application, with its configuration injected or read from the environment."""
    app = FastAPI(
        title="docdoc",
        summary="Structured, validated, traceable data out of documents.",
        version="0.1.0",
    )
    resolved = deployment or _default_deployment()
    _refuse_unnamespaceable(resolved)
    app.state.deployment = resolved
    _require_credential_everywhere_else(app)
    app.include_router(_router())
    # Before the versioned router is irrelevant to routing and relevant to
    # reading: the health routes are not part of the document API and are
    # registered on the application rather than on it (FR-058).
    health.install(app, resolved.readiness())
    _install_error_handler(app)
    _mount_ui(app)
    return app


def _require_credential_everywhere_else(app: FastAPI) -> None:
    """FR-059's exemption list, applied to the whole application.

    The router dependency covers `/v1` and resolves a `Principal` for the
    handlers that need one. It cannot cover what is not on that router, and three
    kinds of thing are not: the `/ui` **mount**, which inherits no dependency
    because a mount is not a route; FastAPI's `/docs`, `/redoc` and
    `/openapi.json`, which its constructor registers directly; and any path a
    later change adds outside the router.

    All three were open on an authenticated deployment, and the pattern is the
    point — every one of them is a thing nobody put on a list, so the fix is a
    rule that needs no list. FR-059 names exactly two exemptions, so this
    enforces exactly two and refuses everything else.

    **Before routing**, which means an unknown path answers 401 rather than 404
    on an authenticated deployment. That is the better answer: a 404 would say
    which paths exist to someone who cannot use any of them.

    With authentication disabled `principal_for` returns the default tenant for
    everyone and this is a function call per request that changes nothing —
    Milestone 8's behaviour, including an open `/ui` (FR-088).
    """

    @app.middleware("http")
    async def _credential(request: Request, call_next: Any) -> Response:
        if request.url.path in _UNAUTHENTICATED:
            return await call_next(request)  # type: ignore[no-any-return]
        try:
            _deployment_of(request).keys.principal_for(
                bearer_of(request.headers.get("authorization"))
            )
        except AuthenticationError as refused:
            # Assembled here rather than raised: an exception handler registered
            # on the application does not run for one raised in middleware, which
            # sits outside that boundary.
            return JSONResponse(
                status_code=401,
                content={
                    "error": {
                        "class": type(refused).__name__,
                        "stage": None,
                        "message": str(refused),
                        "detail": {},
                    }
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)  # type: ignore[no-any-return]


def _refuse_unnamespaceable(deployment: _Deployment) -> None:
    """Refuse to start where authentication is on and the store cannot namespace.

    A deployment constructed from store *objects* rather than a location has one
    store, and one store shared by several tenants is the existence oracle
    ADR-0014 exists to close — every tenant reading and overwriting every other
    tenant's content at identities they can all derive independently.

    Refusing at construction rather than at the first authenticated request is
    the whole point: the alternative fails on one customer's traffic, in
    production, after the previous version has been drained. Principle VIII's no
    silent fallback, applied to a configuration rather than to a stage.

    **``has_store`` is in the condition because a deployment with no store at all
    is a supported one**, and this refused to start it. There is nothing to
    namespace when there is nothing stored: the synchronous routes return their
    results in the response, so no tenant can read another's anything. Without
    that clause, turning authentication on for a storeless deployment raised —
    with a message describing a "given store objects" case that had not
    occurred, which is worse than the refusal, because it sends the operator to
    configure something that was never the problem.
    """
    if deployment.keys.enabled and deployment.has_store and not deployment.can_namespace:
        raise RuntimeError(
            "authentication is enabled but this deployment was given store "
            "objects rather than a location, so it cannot namespace one tenant "
            "away from another. Configure DOCDOC_STORE_ROOT or DOCDOC_STORE_URL"
        )


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

    # **Behind the same credential as everything else** (FR-059), which exempts
    # liveness and readiness and nothing further. A mount is not a route, so it
    # does not inherit the router's dependency and had to be given one.
    #
    # The assets carry no tenant data, so this is not closing a leak — it is
    # keeping one sentence true. Without it a deployment that has enabled
    # authentication still serves the viewer's shell to anyone who can reach the
    # port, and the reader is told the opposite.
    #
    # What it costs is worth stating plainly: the viewer cannot send a bearer
    # token, so with authentication on it does not work either way. Before this
    # it loaded and then failed on every `/v1` call it made; now it does not
    # load. The second is the honest failure — the interface is unavailable, and
    # it says so at the door rather than after the page has rendered.
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


async def principal_of(request: Request) -> Principal:
    """Resolve the caller to exactly one tenant, or refuse (FR-059, FR-060).

    A router-level dependency, so FastAPI resolves it **before** the endpoint
    body runs — which is what makes FR-067 true rather than aspirational. Every
    route in this module reads its request body inside the handler
    (``_read_capped`` streams it), so an unauthenticated request is refused with
    no document read, no provider called, and no store touched.

    With authentication disabled this returns the default tenant for everyone and
    is the one line that makes "one implicit tenant owning all content" a value
    the code holds rather than an assumption three modules make separately
    (FR-088).
    """
    deployment = _deployment_of(request)
    return deployment.keys.principal_for(bearer_of(request.headers.get("authorization")))


#: The caller, resolved before any handler body runs. Spelled as an annotated
#: type rather than as a default argument so that `Depends(...)` is not evaluated
#: at function definition time — the shape ruff's B008 is about, and the one
#: FastAPI now documents.
Caller = Annotated[Principal, Depends(principal_of)]


def _stores_of(request: Request, principal: Principal) -> tuple[Any, Any]:
    """This caller's artifact store and blob store, namespaced to their tenant."""
    return _deployment_of(request).stores_for(principal.tenant_id)


def _router() -> APIRouter:
    # The dependency is on the router rather than on each route, so that a route
    # added later is authenticated by default. The alternative — a decorator per
    # endpoint — makes the safe case the one somebody has to remember, and the
    # failure is silent: the new route simply serves everyone.
    #
    # `/healthz` and `/readyz` are outside this router entirely (FR-058), which
    # is why they are registered on the application in `build_app`.
    router = APIRouter(prefix="/v1", dependencies=[Depends(principal_of)])

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

    @router.post("/documents/{blob_id}/extract")
    async def extract(
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

    # `status_code=202` is the *documented default* for this route; the handler
    # returns 200 on an idempotent replay. Declared rather than omitted so the
    # OpenAPI document names the ordinary case.
    @router.post("/documents/{blob_id}/runs", status_code=202)
    async def submit_run(
        request: Request,
        blob_id: str,
        schema: str,
        principal: Caller,
    ) -> Any:
        """Accept a run and return before any stage executes.

        The response carries a ``run_id`` and **omits** ``processing_id``, which
        does not exist yet and cannot: it is the terminal artifact id, derived
        from stage outputs (ADR-0013 §1). That is the whole reason this is a new
        resource rather than a ``pending`` status on ``GET /v1/jobs``.
        """
        from docdoc.runs.errors import RunStateUnavailableError
        from docdoc.runs.identity import DEFAULT_RETENTION, deadline, new_run_id, now

        deployment = _deployment_of(request)
        if not deployment.has_runs:
            # No `Retry-After`: this one is *not* retryable. Nothing will change
            # until an operator configures a database, and telling a client to
            # come back in a second would make it poll a decision nobody is
            # making. The two 503s on this route mean different things and say so.
            return JSONResponse(
                status_code=503,
                content={
                    "error": {
                        "class": "RunStateUnavailableError",
                        "message": (
                            "this deployment accepts no asynchronous runs; set "
                            f"{RUN_DATABASE_URL_ENV} and apply `docdoc migrate`"
                        ),
                    }
                },
            )
        if not deployment.has_store:
            raise _no_store_configured()

        # Every run records its owning tenant at creation (FR-062), which is why
        # `tenant_id` is a column the first migration creates rather than one a
        # later one adds: a backfill would have to invent an owner for every row.
        tenant = principal.tenant_id
        _, blobs = _stores_of(request, principal)
        assert blobs is not None
        # A malformed identity and an absent one get the same 404. The store
        # raises `ArtifactError` for the first, which would surface as a 500 —
        # and "the server broke" is the wrong thing to tell a caller who typed a
        # bad identifier. The two synchronous routes still answer 500 here; that
        # is pre-existing and FR-009 requires their status codes to be unchanged,
        # so it is reported rather than fixed under this milestone.
        #
        # Another tenant's blob reaches the same branch by being absent from this
        # tenant's namespace, so it gets the same body — no comparison, and
        # therefore nothing to forget (FR-066).
        # `size_of` rather than `get`: this asks whether the document is here,
        # and `get` answered it by reading the whole thing into the API process
        # and discarding it. The metadata route already uses `size_of` for the
        # same question. On a fifty-megabyte scan that is fifty megabytes of
        # resident memory per concurrent submission, bought to compare against
        # `None`.
        #
        # An unreachable store is **not** a 404. That conflation is the one this
        # milestone fixed inside the blob stores, and answering "no such document
        # here" for an outage would reintroduce it one layer up — telling a
        # caller their document is gone when the store is merely down.
        # An unreachable store raises rather than answering `None`, and it is
        # deliberately *not* caught here: `api.errors.status_for` maps
        # `ArtifactError(reason="unavailable")` to 503, so the outage is reported
        # as one from the single place that maps every typed error. Catching it
        # here would be a second such place, and the two would drift.
        if blobs.size_of(blob_id) is None:
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
        allocated = new_run_id()
        try:
            run = deployment.runs().submit(
                spec,
                run_id=allocated,
                now=started,
                expires_at=deadline(started, DEFAULT_RETENTION),
            )
        except RunStateUnavailableError as exc:
            # Refused rather than accepted and dropped (FR-057). A run that
            # cannot be recorded is work that will never be done and never be
            # reported, which is the silent failure this status exists to avoid.
            #
            # `Retry-After` is what makes "retryable" a fact a client can act on
            # rather than a word in a specification. This is the transient case:
            # the database is configured and unreachable, so coming back is the
            # correct thing to do.
            return JSONResponse(
                status_code=503,
                content={"error": {"class": type(exc).__name__, "message": str(exc)}},
                headers={"Retry-After": "1"},
            )

        # **202 for a new run, 200 for one this key already produced**, per
        # contracts/runs-http-api.md. The two codes carry the distinction the
        # body cannot: 202 says work was queued, 200 says the caller is looking
        # at a run that already existed. A client retrying through a flaky
        # network learns whether its first attempt landed — which is the entire
        # reason it sent an idempotency key.
        #
        # Read off the identity rather than from a flag the queue returns: the
        # run that comes back carries the id it was created with, so a run whose
        # id is not the one just allocated is a run that predates this request.
        # That works identically for both queue implementations and needs no
        # second return value to keep in step with the first.
        replayed = run.run_id != allocated
        return JSONResponse(
            status_code=200 if replayed else 202,
            content=RunAcceptedResponse(
                run_id=str(run.run_id),
                status=str(run.status),
                created_at=run.created_at.isoformat(),
            ).model_dump(mode="json"),
        )

    @router.get("/runs/{run_id}", response_model=RunStateResponse)
    async def run_state(request: Request, run_id: str, principal: Caller) -> Any:
        """One of the five states, and never the result itself.

        A succeeded run names its ``processing_id``; the unchanged
        ``GET /v1/jobs/{processing_id}/result`` serves the result. One result
        representation, reachable one way (FR-013).
        """
        from uuid import UUID

        deployment = _deployment_of(request)
        if not deployment.has_runs:
            return _unknown_run(run_id)

        try:
            identity = UUID(run_id)
        except ValueError:
            # A malformed identifier and an unknown one get the same answer: a
            # different one would tell a caller which identifiers are well-formed
            # enough to exist (FR-066).
            return _unknown_run(run_id)

        # The tenant is a predicate in the query, not a check after the fetch
        # (FR-063). Another tenant's run comes back as `None` from the same
        # branch an unknown one does, so the two cannot drift into two answers.
        run = deployment.runs().get(identity, principal.tenant_id)
        if run is None:
            return _unknown_run(run_id)

        return RunStateResponse(**run.dump_public())

    @router.delete("/runs/{run_id}", response_model=RunStateResponse)
    async def cancel_run(request: Request, run_id: str, principal: Caller) -> Any:
        """Request cancellation. Returns the run.

        **A 200 on a running run means *requested*, not *stopped*** (FR-029).
        The worker observes the request at its next stage boundary, and a
        provider call already in flight completes and is billed — so the body
        still reads ``running`` until that boundary is reached. Reporting
        ``cancelled`` here would be the one lie this endpoint must not tell, and
        saying so in the contract is cheaper than letting a caller infer that the
        cancel failed.

        A queued run is cancelled immediately and never executes. A terminal one
        is refused with 409 naming its state (FR-031) rather than silently
        succeeding: a succeeded run has a stored result, and calling it cancelled
        would make a retrievable result unreachable through a lie about its
        history. A run already cancelled is a 200, idempotently (FR-034).
        """
        from uuid import UUID

        from docdoc.runs.errors import RunNotCancellableError, RunNotFoundError
        from docdoc.runs.identity import now

        deployment = _deployment_of(request)
        if not deployment.has_runs:
            return _unknown_run(run_id)

        try:
            identity = UUID(run_id)
        except ValueError:
            return _unknown_run(run_id)

        try:
            # Tenant-scoped in the query, so another tenant's run raises the same
            # `RunNotFoundError` an unknown one does (FR-063, FR-066).
            run = deployment.runs().cancel(identity, principal.tenant_id, now=now())
        except RunNotFoundError:
            return _unknown_run(run_id)
        except RunNotCancellableError as exc:
            return JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "class": type(exc).__name__,
                        "message": str(exc),
                        "detail": {"status": exc.state},
                    }
                },
            )

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
    from docdoc.runs.errors import RunError

    @app.exception_handler(AuthenticationError)
    async def _unauthenticated(request: Request, error: AuthenticationError) -> Response:
        """401, with the same body for absent, malformed, and unrecognised.

        `AuthenticationError` carries no distinction to serialise, which is the
        point: a body that said "malformed" for one and "unknown" for another
        would tell an attacker which guesses are worth refining.

        `WWW-Authenticate` because a 401 without it is not a 401 — RFC 7235
        requires the header, and a client library that follows the specification
        will not retry with a credential it was never told to send.
        """
        return JSONResponse(
            status_code=401,
            content={
                "error": {
                    "class": type(error).__name__,
                    "stage": None,
                    "message": str(error),
                    "detail": {},
                }
            },
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(RunError)
    async def _run_error(request: Request, error: RunError) -> Response:
        """The run layer's errors, which are not `DocdocError`s.

        Deliberately not: the constitution's error model describes documents,
        parsers, schemas, and stages — the things that go wrong *inside* a run —
        and "your database is unreachable" is none of them. So they need their
        own clause here rather than inheriting one that would put them under a
        taxonomy they do not belong to.
        """
        return JSONResponse(
            status_code=_RUN_ERROR_STATUS.get(type(error).__name__, 500),
            content={
                "error": {
                    "class": type(error).__name__,
                    "stage": None,
                    "message": str(error),
                    "detail": {},
                }
            },
        )

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
