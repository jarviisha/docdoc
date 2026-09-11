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

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from docdoc.api import errors as api_errors
from docdoc.api import health
from docdoc.api.auth import (
    ADMIN_SCOPE,
    TENANT_PATTERN,
    AuthenticationError,
    KeyRing,
    Principal,
    bearer_of,
)
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
    LIMIT_CONCURRENT_ENV,
    LIMIT_RUNS_PER_PERIOD_ENV,
    LIMIT_SUBMISSIONS_ENV,
    LIMIT_TOKENS_ENV,
    LIMITS_FILE_ENV,
    OTLP_ENDPOINT_ENV,
    OTLP_HEADERS_ENV,
    PRIORITY_CEILING_ENV,
    REQUEST_BYTES_ENV,
    ROUTING_POLICY_ENV,
    RUN_DATABASE_URL_ENV,
    SCHEMA_PATHS_ENV,
    STORE_ROOT_ENV,
    STORE_URL_ENV,
)
from docdoc.artifacts.paths import root_tenant
from docdoc.runs.health import LIVENESS_PATH, READINESS_PATH
from docdoc.runs.keys import CachedKeyStore, ChainedKeyStore, PostgresKeyStore
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
        limiter: Any = None,
        deliverer: Any = None,
        corrections: Any = None,
        routing: Any = None,
    ) -> None:
        self._deliverer = deliverer
        self._corrections = corrections
        self._routing = routing
        #: Distinguishes "not read yet" from "read, and there is none". Without
        #: it a deployment with no policy would re-read the environment on every
        #: request, which is the per-request configuration read this class exists
        #: to avoid.
        self._routing_read = routing is not None
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
        # The file ring first, the table second (ADR-0016 §7). A deployment
        # migrating keeps its file keys working while table-issued keys begin
        # to, and no key silently stops working because a table appeared.
        #
        # The chain is built even when there is no database: `ChainedKeyStore`
        # with no store resolves through the ring and refuses the three mutating
        # verbs, naming the missing database. That is a better answer than an
        # `AttributeError` from a route that assumed one.
        #: Off unless configured (FR-049). `NullLimiter` is the configuration
        #: docdoc runs in when nobody asked for limits, not a test double.
        self.limiter = limiter if limiter is not None else self._limiter()
        ring = keys if keys is not None else KeyRing.from_environment()
        self.keys = ChainedKeyStore(ring=ring, store=self._key_store())
        #: The ring alone, for the two things only it can answer: whether
        #: authentication is on at all, and what a *plaintext* credential
        #: resolves to when it is off.
        self.ring = ring
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

    def _limiter(self) -> Any:
        """The configured limiter, or the null one (FR-049)."""
        from docdoc.runs.limits import NullLimiter, PostgresLimiter

        policy = _configured_limits()
        if not policy.configured or self._runs is None:
            return NullLimiter()
        execute = getattr(self._runs, "_execute", None)
        if execute is None:
            return NullLimiter()
        return PostgresLimiter(execute=execute, policy=policy)

    def corrections(self) -> Any:
        """The correction store, or ``None``, built once.

        ``None`` whenever there is no database, which is the same condition that
        makes asynchronous runs unavailable — a correction is recorded against a
        run, and a deployment with no runs has nothing to correct.
        """
        if self._corrections is not None:
            return self._corrections
        if self._runs is None:
            return None
        execute = getattr(self._runs, "_execute", None)
        if execute is None:
            return None

        from docdoc.runs.corrections import PostgresCorrectionStore

        self._corrections = PostgresCorrectionStore(execute=execute)
        return self._corrections

    def routing_policy(self) -> Any:
        """The configured routing policy, or ``None``. Read once, at startup.

        ``None`` means `GET /v1/runs/{run_id}` carries **no** `routing` block at
        all — absent rather than null (FR-075). A block saying nothing would
        invite a caller to branch on its absence of content, which is a second
        way of saying "not configured" that nobody documented.
        """
        if self._routing is not None or self._routing_read:
            return self._routing

        self._routing_read = True
        configured = os.environ.get(ROUTING_POLICY_ENV, "").strip()
        if not configured:
            return None

        from pathlib import Path

        from docdoc.runs.routing import policy_from_file

        # Read at startup and never re-read, exactly as the key file and the
        # limits file are: a decision records the policy version that produced
        # it, so two processes disagreeing about what `default@3` means would
        # make the audit trail unreadable.
        self._routing = policy_from_file(Path(configured))
        return self._routing

    def deliverer(self) -> Any:
        """The webhook deliverer, or ``None``, built once.

        ``None`` whenever there is no database or no signing secret configured,
        and both are the default. The API only *registers* destinations and reads
        delivery state; the attempts happen in a worker's maintenance tick, which
        is why nothing here opens a socket (FR-064).
        """
        if self._deliverer is not None:
            return self._deliverer
        if self._runs is None:
            return None
        execute = getattr(self._runs, "_execute", None)
        if execute is None:
            return None

        from docdoc.runs.delivery import PostgresDeliverer, SecretBook
        from docdoc.runs.identity import delivery_allows_private

        book = SecretBook.from_environment()
        if not book.by_digest:
            return None
        self._deliverer = PostgresDeliverer(
            execute=execute, secrets=book, allow_private=delivery_allows_private()
        )
        return self._deliverer

    def _key_store(self) -> Any:
        """A cached, database-backed key store, or ``None``.

        `None` whenever there is no run database — which is the same condition
        that makes asynchronous runs unavailable, and for the same reason: this
        is the milestone where runtime credential management acquired a durable
        dependency (FR-113).
        """
        if self._runs is None:
            return None
        execute = getattr(self._runs, "_execute", None)
        if execute is None:
            return None
        return CachedKeyStore(
            inner=PostgresKeyStore(execute=execute),
            ttl_seconds=_configured_credential_ttl(),
        )

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


def _configured_limits() -> Any:
    """Read the four limits from the environment, all optional (FR-050).

    Absent means the limit does not exist here, which is not the same as zero:
    a deployment that configures none counts nothing and behaves exactly as
    Milestone 9 did.
    """
    from docdoc.runs.limits import LimitPolicy

    def optional(name: str) -> int | None:
        raw = os.environ.get(name, "").strip()
        if not raw:
            return None
        try:
            return max(0, int(raw))
        except ValueError:
            # A malformed tuning knob falls back to "no limit" rather than
            # killing the process, matching `configured_lease`'s reasoning: a
            # typo must not become an outage.
            return None

    default = LimitPolicy(
        submissions_per_minute=optional(LIMIT_SUBMISSIONS_ENV),
        concurrent_runs=optional(LIMIT_CONCURRENT_ENV),
        runs_per_period=optional(LIMIT_RUNS_PER_PERIOD_ENV),
        tokens_per_period=optional(LIMIT_TOKENS_ENV),
    )

    # Per-tenant overrides, if a file names any (FR-050). Read at startup and
    # never re-read, exactly as the key file is: a deployment changing a
    # customer's cap is making a configuration change, and it restarts for every
    # other one.
    configured = os.environ.get(LIMITS_FILE_ENV, "").strip()
    if not configured:
        return default

    from pathlib import Path

    from docdoc.runs.limits import LimitBook

    return LimitBook.from_file(Path(configured), default)


def _configured_credential_ttl() -> int:
    """FR-028's bound, resolved where every other run setting is (FR-096a)."""
    from docdoc.runs.identity import configured_credential_ttl

    return configured_credential_ttl()


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
    # Read once, at startup, beside every other configuration read: a ceiling
    # consulted per request would be one an operator could move without a
    # restart, and every other policy in this deployment needs one.
    _load_ceilings()
    # Only when an endpoint is configured, and only when both observer slots are
    # empty. `telemetry.install` says which of the four things happened, once
    # (research R4, FR-024).
    _install_telemetry()
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

    __slots__ = (
        "blob_id",
        "callback_id",
        "idempotency_key",
        "priority",
        "request_id",
        "schema_identity",
        "tenant_id",
    )

    def __init__(
        self,
        *,
        tenant_id: str,
        blob_id: str,
        schema_identity: str,
        request_id: str | None,
        idempotency_key: str | None,
        #: The **granted** priority, already clamped to the tenant's ceiling.
        #: Clamping happens at this boundary and nowhere below it, so nothing
        #: further down can be used to escalate past another tenant's queue.
        priority: int = 0,
        callback_id: Any = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.blob_id = blob_id
        self.schema_identity = schema_identity
        self.request_id = request_id
        self.idempotency_key = idempotency_key
        self.priority = priority
        self.callback_id = callback_id


#: Returned by `_requested_callback` for a callback that is unknown, malformed,
#: or another tenant's. A sentinel rather than `None`, because `None` is the
#: ordinary answer — most submissions ask to be notified about nothing.
_UNKNOWN_CALLBACK = object()


def _granted_priority(requested: Any, *, tenant_id: str) -> int | None:
    """The priority this run gets, or ``None`` when the request was malformed.

    **Over the ceiling is accepted at the ceiling, never refused** (FR-087a). An
    operator lowering a ceiling must not break a client that changed nothing, and
    a client that asked for `urgent` on a deployment that grants none should get
    its run executed rather than a 4xx it cannot act on.

    A *malformed* value is different and is refused: `priority: "high"` is a
    client that believes it is asking for something, and silently granting
    `ordinary` would leave that belief in place for ever.
    """
    from docdoc.runs.model import Priority

    ceiling = _configured_priority_ceiling(tenant_id)
    if requested is None:
        return min(int(Priority.ORDINARY), ceiling)

    try:
        asked = Priority.from_label(str(requested))
    except KeyError:
        return None
    return min(int(asked), ceiling)


def _configured_priority_ceiling(tenant_id: str) -> int:
    """The highest priority this tenant may be granted (FR-087b).

    Deployment-wide from the environment, per-tenant from the same limits file,
    and **reachable through no route in either form**. A tenant that could raise
    its own ceiling has self-service escalation past another customer's queue,
    which is the whole reason a ceiling exists rather than letting a client pick
    freely.

    Defaults to `ORDINARY`: a deployment that configures nothing grants nothing,
    so every run is claimed in creation order exactly as Milestone 9 claimed it
    (FR-091).
    """
    from docdoc.runs.model import Priority

    raw = os.environ.get(PRIORITY_CEILING_ENV, "").strip().lower()
    default = {"ordinary": int(Priority.ORDINARY), "urgent": int(Priority.URGENT)}.get(
        raw, int(Priority.ORDINARY)
    )
    return _CEILINGS.get(tenant_id, default)


#: Per-tenant ceilings, read once at import for the reason the key file and the
#: limits file are read once: a deployment changing a customer's ceiling is
#: making a configuration change, and it restarts for every other one.
_CEILINGS: dict[str, int] = {}


def _load_ceilings() -> None:
    """Read the per-tenant ceilings out of the limits file, if it names any.

    The same file the four limits use, because a ceiling *is* a limit and a
    second per-tenant file would be a second place for a customer's policy to
    live. Unknown keys there are already refused by `LimitBook`, so this reads
    its own optional key rather than extending that model with a fifth field
    nothing else counts.
    """
    configured = os.environ.get(LIMITS_FILE_ENV, "").strip()
    _CEILINGS.clear()
    if not configured:
        return

    from pathlib import Path

    from docdoc.runs.model import Priority

    try:
        raw = json.loads(Path(configured).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        # `LimitBook.from_file` reads the same file and refuses loudly. Failing
        # again here would report one configuration mistake twice; the ceiling
        # falls back to the deployment-wide default, which grants nothing.
        return

    by_name = {"ordinary": int(Priority.ORDINARY), "urgent": int(Priority.URGENT)}
    for tenant_id, entry in (raw.get("tenants") or {}).items():
        name = str((entry or {}).get("priority_ceiling", "")).strip().lower()
        if name in by_name:
            _CEILINGS[tenant_id] = by_name[name]


def _requested_callback(deployment: _Deployment, requested: Any, *, tenant_id: str) -> Any:
    """The callback this submission names, ``None``, or `_UNKNOWN_CALLBACK`.

    Tenant-scoped **in the query** rather than checked after the fetch, which is
    the rule every other lookup in this module follows: a caller cannot forget a
    check that does not exist.
    """
    from uuid import UUID

    if requested is None:
        return None

    deliverer = deployment.deliverer()
    if deliverer is None:
        return _UNKNOWN_CALLBACK

    try:
        identity = UUID(str(requested))
    except ValueError:
        return _UNKNOWN_CALLBACK

    found = deliverer.callback(identity, tenant_id=tenant_id)
    return _UNKNOWN_CALLBACK if found is None else identity


def _routing_for(request: Request, run: Any) -> Any:
    """The routing decision for a completed run, or ``None`` (FR-075, FR-076).

    ``None`` — and therefore an absent block — whenever no policy is configured,
    the run has not succeeded, or the result cannot be read back. All three are
    "there is no decision to report", and reporting an empty one would be a
    routing outcome nobody computed.

    **Computed on read rather than stored** (FR-074's other half). A decision
    carries the version of the policy that produced it, and `runs` gains no
    column for one: an artifact carrying a decision would make `processing_id` a
    function of a threshold, so editing a policy would change the identity of
    results computed before the edit. Reading costs two artifact fetches, and
    only on a deployment that asked for routing by configuring a policy.
    """
    from docdoc.runs.model import RunStatus

    deployment = _deployment_of(request)
    policy = deployment.routing_policy()
    if policy is None or run.status is not RunStatus.SUCCEEDED or run.processing_id is None:
        return None

    from docdoc.runs.routing import StoredResult, decide

    store, _ = deployment.stores_for(run.tenant_id)
    validation = _terminal(store, run.processing_id)
    if validation is None:
        return None

    from docdoc.grounding.result import GroundingResult
    from docdoc.pipeline.stages import Stage, spec_for

    grounding = None
    if store is not None:
        grounding = store.get(
            validation.provenance.grounding_artifact_id,
            model=GroundingResult,
            artifact_format_version=spec_for(Stage.GROUND).artifact_format_version,
        )

    # `StoredResult` and **not** a fabricated `PipelineResult`. This line built
    # one, with `outcomes=()` and `provenance=validation.provenance` — and those
    # are different models, so pydantic rejected it and this route raised on
    # every succeeded run once a policy was configured. T179 is the first test
    # that exercised it.
    #
    # The wider lesson is the one `_stored_result` already records: a retrieval
    # is not a run, and assembling one that claims to be is how a type gets
    # satisfied by a fiction.
    return decide(StoredResult(grounding=grounding, validation=validation), policy)


def _install_telemetry() -> str:
    """Bind the OTLP bridge, if a deployment asked for one (FR-018).

    Unset means nothing is exported and **no telemetry dependency is required**,
    which is why this reads the environment rather than importing anything: the
    import that would fail on a base install happens inside `telemetry.bridge`,
    and only when there is an endpoint to export to.
    """
    from docdoc.runs.observe import install_bridge
    from docdoc.telemetry import bridge, configure_logging, parse_headers

    # **Before anything else observability-shaped**, because until this ran the
    # events below went nowhere: `docdoc.*` had no handler and inherited
    # `WARNING` from a root logger that had none either, so every
    # `run.transition`, `credential.operation`, and `limit.refused` was formatted
    # and dropped. Found by following quickstart scenario 6, which tells the
    # operator to read a startup line that was never printed.
    configure_logging()

    endpoint = os.environ.get(OTLP_ENDPOINT_ENV, "")
    headers = os.environ.get(OTLP_HEADERS_ENV, "")
    # A thunk, so the exporter is constructed only once the slot check has
    # passed — and so `opentelemetry` is imported only on a deployment that asked
    # for it (R5).
    return install_bridge(
        lambda: bridge(endpoint=endpoint, headers=parse_headers(headers)),
        endpoint=endpoint,
    )


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


async def administrator_of(request: Request) -> Principal:
    """A caller carrying the `admin` scope, or a 404 (FR-031, ADR-0016 §6).

    **404 and not 403**, which is the decision worth reading twice. A `403` tells
    a caller that an administrative surface exists at this URL and that their key
    is the only thing missing; that is a disclosure with no upside, and it is the
    same reasoning `AuthenticationError` already follows in refusing to
    distinguish absent from malformed from unrecognised.

    So an ordinary tenant credential gets exactly what it would get for a route
    that does not exist.

    A deployment with authentication **off** has one implicit tenant and no
    scopes at all, so every admin route is unreachable there — which is correct:
    erasing a tenant on a deployment that has never named one is not an operation
    anybody should be able to invoke through HTTP.
    """
    principal = await principal_of(request)
    if not principal.has(ADMIN_SCOPE):
        raise HTTPException(
            status_code=404, detail={"error": {"class": "NotFound", "message": "not found"}}
        )
    return principal


#: Resolved before any admin handler body runs, on the same terms as `Caller`.
Administrator = Annotated[Principal, Depends(administrator_of)]


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

        # **Before the store is touched and before any row exists** (FR-044). A
        # refused submission costs the deployment one counter read: no run, no
        # queue position, no blob lookup.
        #
        # After authentication, deliberately. A limit is a fact about a tenant,
        # so there has to be one; and an unauthenticated caller learning which
        # tenant is over quota would be a disclosure the 429 body is careful not
        # to make to anyone else.
        verdict = deployment.limiter.check(tenant_id=tenant, now=now())
        if not verdict.allowed:
            return JSONResponse(
                status_code=429,
                headers={"Retry-After": str(verdict.retry_after_seconds)},
                content={
                    "error": "limit_exceeded",
                    "limit": verdict.limit,
                    "observed": verdict.observed,
                    "allowed": verdict.permitted,
                    "retry_after_seconds": verdict.retry_after_seconds,
                },
            )

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

        # **Two optional inputs, and an empty body is the Milestone 9 request.**
        # Read after the limit check and after the blob lookup, so a refused
        # submission costs no parsing either.
        body = await _read_json(request)

        granted = _granted_priority(body.get("priority"), tenant_id=tenant)
        if granted is None:
            return JSONResponse(
                status_code=422,
                content={
                    "error": "invalid_priority",
                    "detail": "priority is `ordinary` or `urgent`",
                },
            )

        callback_id = _requested_callback(deployment, body.get("callback_id"), tenant_id=tenant)
        if callback_id is _UNKNOWN_CALLBACK:
            # Unknown, malformed, and another tenant's are one answer, exactly as
            # they are for a run: a different one would tell a caller which
            # callback identifiers exist under somebody else's key.
            return JSONResponse(
                status_code=404,
                content={"error": {"class": "UnknownCallback", "message": "no such callback here"}},
            )

        spec = _RunSpec(
            tenant_id=tenant,
            blob_id=blob_id,
            schema_identity=schema,
            request_id=request.headers.get("x-request-id"),
            idempotency_key=request.headers.get("idempotency-key"),
            priority=granted,
            callback_id=callback_id,
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
        if not replayed:
            # Counted **after** the row exists, and only for a run that is new.
            # A replayed idempotency key is one submission the caller already
            # paid for, and counting it twice would make a client retrying
            # through a flaky network spend its allowance on a run it already
            # has (FR-011).
            deployment.limiter.record_submission(tenant_id=tenant, now=started)

        return JSONResponse(
            status_code=200 if replayed else 202,
            content=RunAcceptedResponse(
                run_id=str(run.run_id),
                status=str(run.status),
                created_at=run.created_at.isoformat(),
                # From the run and not from `granted`: on an idempotent replay
                # the caller is looking at a run that already existed, and what
                # they need to know is the priority *that* run carries rather
                # than the one this request asked for and did not get.
                # The **name**, because that is what the request used. See
                # `Priority.label`.
                priority=run.priority.label,
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
            # Two lookups, two outcomes, and they differ **in kind** (FR-011).
            #
            # A tombstone means: this was yours and it is gone. The owner is told
            # when, and under which policy, and nothing else -- a tombstone holds
            # four fields precisely so that being told about one discloses
            # nothing about what it held.
            #
            # To every other tenant the tombstone lookup misses too, because it
            # is scoped in the query exactly as `get` is, so they fall through to
            # the same 404 an identifier that never existed produces. That is
            # Milestone 9's FR-066 unchanged: nothing here is a way to prove a
            # run existed under somebody else's key.
            stone = deployment.runs().tombstone(identity, principal.tenant_id)
            if stone is None:
                return _unknown_run(run_id)
            return JSONResponse(
                status_code=410,
                content={
                    "error": "run_erased",
                    "run_id": str(stone.run_id),
                    "deleted_at": stone.deleted_at.isoformat(),
                    "policy": stone.policy,
                },
            )

        body = run.dump_public()
        decision = _routing_for(request, run)
        if decision is None:
            return RunStateResponse(**body)

        # A `JSONResponse` rather than a wider model, so `routing` is **absent**
        # and not null when no policy is configured (FR-075). A response model
        # carrying an optional field would emit `"routing": null`, which is a
        # second way of saying "not configured" that nobody documented and that a
        # client would eventually branch on.
        return JSONResponse(
            status_code=200,
            content={**body, "routing": decision.model_dump(mode="json")},
        )

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

    # -- corrections (ADR-0017, Principle IX) --------------------------------
    #
    # Two routes: record one, read this tenant's. There is no third, and the
    # absence is the contract (FR-083). No assignment, no reviewer queue, no work
    # list, no review state, and no `PATCH` on a result — Principle IX permits
    # the model and forbids the platform, and "a full review UI" is on the
    # deferred-technology list by name.

    @router.post("/runs/{run_id}/corrections", status_code=201)
    async def record_correction(request: Request, run_id: str, principal: Caller) -> Any:
        """Record a reviewer's statement that a value was wrong (FR-078).

        **The result, its artifacts, and its identities are unchanged.** A
        correction sits beside what it annotates; if recording one edited the
        artifact it described, the recorded pipeline output would become a
        function of who reviewed it and the whole ADR-0003 chain would be
        describing a run that never happened.

        `annotator` comes from the **body** and never from the credential
        (FR-085). The person who reviewed a value and the key that submitted it
        are different facts, and a deployment where one operations key posts
        every reviewer's corrections is the ordinary case rather than the strange
        one.
        """
        from uuid import UUID

        from docdoc.evaluation.corrections import Correction
        from docdoc.runs.identity import configured_correction_retention, now
        from docdoc.runs.model import RunStatus

        deployment = _deployment_of(request)
        store = deployment.corrections()
        if store is None or not deployment.has_runs:
            return _unknown_run(run_id)

        try:
            identity = UUID(run_id)
        except ValueError:
            return _unknown_run(run_id)

        # Tenant-scoped in the query rather than checked after the fetch, which
        # is what makes another tenant's run indistinguishable from one that
        # never existed (FR-079).
        run = deployment.runs().get(identity, principal.tenant_id)
        if run is None:
            stone = deployment.runs().tombstone(identity, principal.tenant_id)
            if stone is None:
                return _unknown_run(run_id)
            # `410` and not `404`, and naming which: an annotation against
            # something that is gone is uninterpretable, and a reviewer who typed
            # the right identifier should not be told they typed a wrong one
            # (FR-082).
            return JSONResponse(
                status_code=410,
                content={
                    "error": "run_erased",
                    "run_id": str(stone.run_id),
                    "deleted_at": stone.deleted_at.isoformat(),
                    "policy": stone.policy,
                },
            )

        if run.status is not RunStatus.SUCCEEDED:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "run_has_no_result",
                    "detail": f"this run is {run.status} and has no result to correct",
                    "status": str(run.status),
                },
            )

        body = await _read_json(request)
        try:
            correction = Correction.model_validate(body)
        except Exception as error:
            # The class and the field, never the body: a validation message from
            # a correction quotes the value a reviewer typed, which is document
            # content by another route.
            return JSONResponse(
                status_code=422,
                content={"error": "invalid_correction", "detail": type(error).__name__},
            )

        at = now()
        period = configured_correction_retention()
        # **The longer of the two deadlines wins** (FR-013). A correction is
        # evidence about a result, so a policy that expired it before the result
        # would leave the result unexplained; one that expired the result first
        # is prevented by `pinned_runs`.
        expires_at = max(run.expires_at, at + period) if period else run.expires_at

        correction_id = store.record(
            correction,
            tenant_id=principal.tenant_id,
            run_id=identity,
            at=at,
            expires_at=expires_at,
        )
        return JSONResponse(
            status_code=201,
            content={
                "correction_id": str(correction_id),
                "run_id": str(identity),
                "field_path": correction.field_path,
                "expires_at": expires_at.isoformat(),
            },
        )

    @router.get("/runs/{run_id}/corrections")
    async def list_corrections(request: Request, run_id: str, principal: Caller) -> Any:
        """This tenant's corrections against this run, and nobody else's (FR-079)."""
        from uuid import UUID

        deployment = _deployment_of(request)
        store = deployment.corrections()
        if store is None:
            return _unknown_run(run_id)

        try:
            identity = UUID(run_id)
        except ValueError:
            return _unknown_run(run_id)

        return {
            "corrections": [
                correction.model_dump(mode="json")
                for correction in store.for_run(tenant_id=principal.tenant_id, run_id=identity)
            ]
        }

    # -- callbacks (ADR-0018) -----------------------------------------------

    @router.post("/callbacks", status_code=201)
    async def register_callback(request: Request, principal: Caller) -> Any:
        """Register a destination. **The secret is never returned** (FR-066).

        The destination is validated here *and* again before every attempt. Only
        the second closes DNS rebinding; this one exists so an operator finds out
        about a refused destination while they are looking at the response rather
        than in a delivery that quietly never happens.
        """
        from docdoc.runs.delivery import DeliveryError
        from docdoc.runs.identity import new_credential_id, now

        deployment = _deployment_of(request)
        deliverer = deployment.deliverer()
        if deliverer is None:
            return _delivery_unconfigured()

        body = await _read_json(request)
        url = str(body.get("url", "")).strip()
        secret = str(body.get("secret", "")).strip()
        if not url or not secret:
            raise HTTPException(
                status_code=422,
                detail={"error": "invalid_callback", "detail": "url and secret are required"},
            )

        try:
            callback = deliverer.register(
                tenant_id=principal.tenant_id,
                url=url,
                secret=secret,
                at=now(),
                # A callback identity is an identity like any other. `identity`
                # is the only module permitted to allocate one, and reusing the
                # credential allocator rather than adding a seventh function that
                # calls `uuid4` is the whole of what that rule buys.
                callback_id=new_credential_id(),
            )
        except DeliveryError as error:
            # The class of refusal and **not the addresses it resolved to**. A
            # 422 that reported them would make this route a name resolver for
            # an authenticated caller, which is a capability nobody asked for.
            return JSONResponse(
                status_code=422,
                content={"error": "destination_refused", "detail": str(error)},
            )

        return JSONResponse(
            status_code=201,
            content={
                "callback_id": str(callback.callback_id),
                "url": callback.url,
                "created_at": _iso(callback.created_at),
            },
        )

    @router.delete("/callbacks/{callback_id}", status_code=204, response_class=Response)
    async def revoke_callback(request: Request, callback_id: str, principal: Caller) -> Response:
        """`204`, idempotent — the same shape credential revocation has."""
        from uuid import UUID

        from docdoc.runs.identity import now

        deployment = _deployment_of(request)
        deliverer = deployment.deliverer()
        if deliverer is None:
            return Response(status_code=204)

        try:
            identity = UUID(callback_id)
        except ValueError:
            # Malformed and unknown are one answer here for the reason they are
            # one on every other route: a different one names which identifiers
            # are well-formed enough to exist.
            return Response(status_code=204)

        deliverer.revoke(identity, tenant_id=principal.tenant_id, at=now())
        return Response(status_code=204)

    @router.get("/runs/{run_id}/delivery")
    async def run_delivery(request: Request, run_id: str, principal: Caller) -> Any:
        """What became of the notification for this run (FR-059).

        `404` when no callback was registered: an absent delivery is not an
        error, and it is what a caller who never asked to be notified should be
        told about a resource that was never created.

        `last_error` is a **class name**, which is the rule `error_class` follows
        on a run and for the same reason — a receiver's response body is somebody
        else's content.
        """
        from uuid import UUID

        deployment = _deployment_of(request)
        deliverer = deployment.deliverer()
        try:
            identity = UUID(run_id)
        except ValueError:
            return _unknown_run(run_id)
        if deliverer is None:
            return _unknown_run(run_id)

        delivery = deliverer.for_run(run_id=identity, tenant_id=principal.tenant_id)
        if delivery is None:
            return _unknown_run(run_id)
        return {
            "delivery_id": str(delivery.delivery_id),
            "state": str(delivery.state),
            "attempts": delivery.attempts,
            "last_status": delivery.last_status,
            "last_error": delivery.last_error,
            "next_attempt_at": _iso(delivery.next_attempt_at),
        }

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

    # -- administrative routes (Milestone 10) ---------------------------------
    #
    # Every one of these requires the `admin` scope, and a caller without it gets
    # a 404 rather than a 403 — see `administrator_of`.

    @router.post("/admin/credentials", status_code=201)
    async def issue_credential(request: Request, principal: Administrator) -> Any:
        """Issue a credential. **The plaintext is returned exactly once** (FR-025).

        There is no route that returns it again and none can be added without
        changing what "stored as a digest" means -- the table holds
        ``sha256(key)`` and nothing else.

        ``scopes: ["admin"]`` is **refused here** (FR-032). The first
        administrative credential is created by ``docdoc credential issue
        --admin`` against the database, because a route that can mint an
        administrative key is a route that needs no credential -- which is the
        hole this whole feature exists to close.
        """
        from docdoc.runs.identity import now

        body = await _read_json(request)
        tenant_id = str(body.get("tenant_id", "")).strip()
        if not TENANT_PATTERN.match(tenant_id):
            raise HTTPException(status_code=422, detail={"error": "invalid_tenant_id"})

        requested = frozenset(body.get("scopes") or ())
        if ADMIN_SCOPE in requested:
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "admin_scope_not_issuable",
                    "detail": (
                        "an administrative credential is created by `docdoc "
                        "credential issue --admin` against the database. A route "
                        "that could mint one is a route that needs no credential"
                    ),
                },
            )

        deployment = _deployment_of(request)
        secret, credential = deployment.keys.issue(
            tenant_id=tenant_id,
            scopes=requested,
            label=(body.get("label") or None),
            now=now(),
        )
        _audit(actor=principal.tenant_id, operation="issue", credential=credential)
        return JSONResponse(
            status_code=201,
            content={
                "credential_id": str(credential.credential_id),
                "tenant_id": credential.tenant_id,
                "key": secret,
                "created_at": credential.created_at.isoformat() if credential.created_at else None,
            },
        )

    @router.get("/admin/credentials")
    async def list_credentials(request: Request, tenant_id: str, principal: Administrator) -> Any:
        """What has been issued. **No key and no digest** (FR-030).

        A listing exists so an operator can see what they hold and revoke one of
        them; either field would make it a way to obtain what it describes.
        """
        deployment = _deployment_of(request)
        return {
            "credentials": [
                {
                    "credential_id": str(credential.credential_id),
                    "tenant_id": credential.tenant_id,
                    "label": credential.label,
                    "scopes": sorted(credential.scopes),
                    "created_at": _iso(credential.created_at),
                    "last_used_at": _iso(credential.last_used_at),
                    "revoked_at": _iso(credential.revoked_at),
                }
                for credential in deployment.keys.list_for(tenant_id)
            ]
        }

    # `response_class=Response` because FastAPI otherwise infers a response model
    # from the annotation and then refuses the route: a 204 carries no body.
    @router.delete("/admin/credentials/{credential_id}", status_code=204, response_class=Response)
    async def revoke_credential(
        request: Request, credential_id: str, principal: Administrator
    ) -> Response:
        """Revoke, and say nothing. `204`, idempotent (ADR-0016 §2).

        Effective on every process within the configured cache lifetime, with no
        restart -- which is the defect Milestone 9 documented and did not fix.

        Revoking an already-revoked credential is also `204`. An operator
        reacting to a leak runs this twice, and the second attempt failing would
        tell them something had gone wrong when nothing had.
        """
        from uuid import UUID

        from docdoc.runs.identity import now

        try:
            identity = UUID(credential_id)
        except ValueError:
            raise HTTPException(status_code=404, detail={"error": "not_found"}) from None

        deployment = _deployment_of(request)
        deployment.keys.revoke(identity, now=now())
        _audit(actor=principal.tenant_id, operation="revoke", credential_id=identity)
        return Response(status_code=204)

    @router.delete("/admin/tenants/{tenant_id}")
    async def erase_tenant(request: Request, tenant_id: str, principal: Administrator) -> Any:
        """Remove a customer's data, on request (FR-005).

        **The default tenant is refused here, always** (ADR-0015 §5). Its
        namespace is the store root, so an erasure of it removes everything
        written before authentication was enabled and everything ``docdoc
        extract`` ever wrote — none of which belongs to the customer being
        erased. That reading of "erase the default tenant" exists on the command
        line, behind a flag somebody has to type, and at **no URL**.

        **`200` and synchronous**, which is a correction. This route first
        returned `202` and claimed the maintenance tick would do the work; the
        tick sweeps and has never had an erasure to perform, so the route
        answered "accepted" and did nothing — the failure spec.md names in as
        many words: *"a deployment that answers 'erased' while the bytes are
        still in the bucket — an answer that is worse than refusing, because it
        is believed."*

        The `202` design needed a request table, a migration, a tick step, and a
        status route to be useful, for an operation an operator invokes rarely
        and waits for. `docdoc erase` already does the work synchronously; two
        paths doing one thing the same way beat one path doing it through a queue
        nothing else uses. The bound is real and stated: at most
        `_ERASURE_RUN_LIMIT` runs per call, and a tenant with more needs the
        command line.
        """
        deployment = _deployment_of(request)
        if not deployment.has_runs:
            return _unknown_run(tenant_id)

        if tenant_id == root_tenant():
            return JSONResponse(
                status_code=409,
                content={
                    "error": "default_tenant_erasure_refused",
                    "detail": (
                        "the default tenant's namespace is the store root, so this "
                        "would remove content no run ever produced. Use `docdoc "
                        "erase --tenant " + tenant_id + "` for its run-derived "
                        "content, or --purge-store-root for the whole store"
                    ),
                },
            )

        return _erase(request, tenant_id=tenant_id, blob_id=None)

    @router.delete("/admin/documents/{blob_id}")
    async def erase_document(request: Request, blob_id: str, principal: Administrator) -> Any:
        """Remove one document and the runs over it (FR-005).

        Scoped to the administrator's own tenant. A prefix cannot express "one
        blob", so this is always the set difference — and the default tenant
        needs no exception here for the same reason.
        """
        return _erase(request, tenant_id=principal.tenant_id, blob_id=blob_id)

    return router


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _delivery_unconfigured() -> JSONResponse:
    """`503`, naming what to configure — the shape the run routes already use.

    Not a `404`: the route exists, and telling a caller it does not would send
    them looking for a typo in a URL that is correct. Not a `501` either; this is
    a deployment that has not configured a capability, which is the same thing
    `RunStateUnavailableError` reports about a missing database.
    """
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "class": "DeliveryError",
                "message": (
                    "this deployment delivers no callbacks; set "
                    "DOCDOC_DELIVERY_SECRETS_FILE and a run-state database. Run "
                    "state remains readable by polling, which is the record "
                    "either way"
                ),
            }
        },
    )


async def _read_json(request: Request) -> dict[str, Any]:
    """A small JSON body, or an empty mapping."""
    import json

    raw = await request.body()
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        raise HTTPException(status_code=422, detail={"error": "invalid_json"}) from None
    return parsed if isinstance(parsed, dict) else {}


def _audit(
    *,
    actor: str,
    operation: str,
    credential: Any = None,
    credential_id: Any = None,
) -> None:
    """One structured event per credential operation (FR-035).

    Actor, operation, credential identifier, tenant -- and **never the
    credential**. An audit trail that recorded what it was auditing would be the
    single richest place to steal from in the deployment.
    """
    identity = credential_id if credential is None else credential.credential_id
    logging.getLogger("docdoc.api").info(
        "credential.operation",
        extra={
            "docdoc": {
                "event": "credential.operation",
                "actor": actor,
                "operation": operation,
                "credential_id": str(identity) if identity else None,
                "tenant_id": None if credential is None else credential.tenant_id,
            }
        },
    )


#: How many of a tenant's runs one HTTP erasure removes. A bound rather than a
#: promise of completeness: the route is synchronous, so an unbounded erasure
#: would hold a connection for as long as a tenant is large. An operator with
#: more than this runs `docdoc erase`, which is bounded only by their patience.
_ERASURE_RUN_LIMIT = 10_000


def _erase(request: Request, *, tenant_id: str, blob_id: str | None) -> JSONResponse:
    """Erase, and report what went (FR-005, FR-009, FR-012).

    Idempotent: erasing a tenant that never existed succeeds having removed
    nothing, because an operator acting under time pressure runs this twice and
    neither run should fail.
    """
    from docdoc.runs import retention
    from docdoc.runs.identity import now

    deployment = _deployment_of(request)
    artifacts, blobs = deployment.stores_for(tenant_id)
    if artifacts is None:
        raise _no_store_configured()

    report = retention.erase(
        deployment.runs(),
        retention.Stores(artifacts=artifacts, blobs=blobs),
        tenant_id=tenant_id,
        now=now(),
        blob_id=blob_id,
        limit=_ERASURE_RUN_LIMIT,
        # Never from here. The store root belongs to no customer, and the route
        # above refuses the default tenant before reaching this (ADR-0015 §5).
        is_default_tenant=tenant_id == root_tenant(),
        allow_store_root=False,
    )

    return JSONResponse(
        status_code=200,
        content={
            "scope": "document" if blob_id else "tenant",
            "target": blob_id or tenant_id,
            "runs": report.runs,
            "artifacts": report.artifacts,
            "blobs": report.blobs,
            "rows": report.rows,
            "degraded": report.degraded,
        },
    )


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
