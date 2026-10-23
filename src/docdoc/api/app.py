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

from fastapi import APIRouter, Depends, FastAPI, Request, Response
from fastapi.responses import JSONResponse

from docdoc.api import errors as api_errors
from docdoc.api import health
from docdoc.api.admin import routes as admin_routes
from docdoc.api.auth import AuthenticationError, KeyRing, bearer_of
from docdoc.api.console import console_gate, console_router, is_console_api
from docdoc.api.deps import (
    _OCR_QUEUE_DEFAULT,
    _OCR_QUEUES,
    _deployment_of,
    _load_ceilings,
    principal_of,
)
from docdoc.api.documents import jobs_routes
from docdoc.api.documents import routes as document_routes
from docdoc.api.evaluations import routes as evaluation_routes
from docdoc.api.ocr import routes as ocr_routes
from docdoc.api.runs import routes as run_routes
from docdoc.api.settings import (
    DEFAULT_MAX_REQUEST_BYTES,
    DEFAULT_OCR_QUEUE_PER_TENANT,
    DEFAULT_OCR_QUEUE_TOTAL,
    LIMIT_CONCURRENT_ENV,
    LIMIT_RUNS_PER_PERIOD_ENV,
    LIMIT_SUBMISSIONS_ENV,
    LIMIT_TOKENS_ENV,
    LIMITS_FILE_ENV,
    OCR_QUEUE_PER_TENANT_ENV,
    OCR_QUEUE_TOTAL_ENV,
    OTLP_ENDPOINT_ENV,
    OTLP_HEADERS_ENV,
    REQUEST_BYTES_ENV,
    ROUTING_POLICY_ENV,
    RUN_DATABASE_URL_ENV,
    SCHEMA_PATHS_ENV,
    STORE_ROOT_ENV,
    STORE_URL_ENV,
)
from docdoc.runs.health import LIVENESS_PATH, READINESS_PATH
from docdoc.runs.keys import CachedKeyStore, ChainedKeyStore, PostgresKeyStore
from docdoc.runs.model import DEFAULT_TENANT

if TYPE_CHECKING:
    from docdoc.artifacts import ArtifactStore, BlobStore
    from docdoc.ingest.registry import ParserRegistry

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

#: The console's shell, and the one prefix exempt from the credential.
#:
#: **A browser's top-level navigation carries no `Authorization` header.** The
#: viewer's mount was gated and recorded what that costs: with authentication on,
#: it did not load. That was the honest failure for a surface whose credential is
#: a deployment-wide one; it is a fatal one for a console whose entire premise is
#: that an operator pastes a key **into a page that has already loaded**
#: (specs/011 FR-006, research R1). The viewer is gone (ADR-0020); the reason it
#: could not be copied here is why this prefix exists and so it is kept.
#:
#: What is exempt is HTML, CSS, and JavaScript. No run, no tenant identifier, no
#: credential, and no document is reachable from those bytes, and the console
#: issues zero requests before a key is entered. Every `/v1` call it then makes
#: is authenticated exactly as any other client's is.
#:
#: A prefix and not a path, because a built client is a directory of hashed
#: assets. It is checked with `startswith` against this exact string plus the
#: bare path, so a route named `/consoleroom` later would not be exempted by
#: accident.
#:
#: **Not to be confused with `/v1/console`** (ADR-0023 §3), the console's own API.
#: That is not exempt: it skips the Bearer check only to face its own session
#: gate, `docdoc.api.console.console_gate`. Since ADR-0023 the shell no longer
#: takes a pasted key — it signs in there.
_CONSOLE_PREFIX = "/console"


def _is_console_asset(path: str) -> bool:
    """Whether a path is the console's shell rather than the API."""
    return path == _CONSOLE_PREFIX or path.startswith(f"{_CONSOLE_PREFIX}/")


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
        parsers: ParserRegistry | None = None,
        adapter: Any = None,
        max_request_bytes: int | None = None,
        limits: Any = None,
        runs: Any = None,
        keys: Any = None,
        limiter: Any = None,
        deliverer: Any = None,
        corrections: Any = None,
        schemas: Any = None,
        routing: Any = None,
        #: The console accounts store (ADR-0023 §1). `None` builds one from the
        #: run-state database, see `console_accounts`.
        console: Any = None,
        #: The two OCR caps (ADR-0022). `None` reads the environment, then the
        #: default -- both are on with no configuration.
        ocr_queue_per_tenant: int | None = None,
        ocr_queue_total: int | None = None,
    ) -> None:
        self._deliverer = deliverer
        self.ocr_queue_per_tenant = (
            ocr_queue_per_tenant
            if ocr_queue_per_tenant is not None
            else _configured_cap(OCR_QUEUE_PER_TENANT_ENV, DEFAULT_OCR_QUEUE_PER_TENANT)
        )
        self.ocr_queue_total = (
            ocr_queue_total
            if ocr_queue_total is not None
            else _configured_cap(OCR_QUEUE_TOTAL_ENV, DEFAULT_OCR_QUEUE_TOTAL)
        )
        self._corrections = corrections
        self._evaluations: Any = None
        self._schemas = schemas
        self._console = console
        #: The console's idle bound (ADR-0023 §1), read once like every other
        #: configuration value here rather than per gated request.
        from docdoc.runs.identity import configured_console_idle

        self.console_idle = configured_console_idle()
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
        self._parsers = parsers
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

    def ocr_queue_for(self, tenant_id: str) -> int:
        """This tenant's OCR cap: its `ocr_queue` in the limits file, or the default."""
        default = _OCR_QUEUE_DEFAULT[0] if _OCR_QUEUE_DEFAULT else self.ocr_queue_per_tenant
        return _OCR_QUEUES.get(tenant_id, default)

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

    def evaluations(self) -> Any:
        """The evaluation store, or ``None`` with no database, built once like `corrections`."""
        if self._evaluations is not None:
            return self._evaluations
        execute = getattr(self._runs, "_execute", None)
        if execute is None:
            return None

        from docdoc.runs.evaluations import PostgresEvaluationStore

        self._evaluations = PostgresEvaluationStore(execute=execute)
        return self._evaluations

    def console_schemas(self) -> Any:
        """The console schema store, or ``None`` with no database, built once like `evaluations`."""
        if self._schemas is not None:
            return self._schemas
        execute = getattr(self._runs, "_execute", None)
        if execute is None:
            return None

        from docdoc.runs.console_schemas import PostgresSchemaStore

        self._schemas = PostgresSchemaStore(execute=execute)
        return self._schemas

    def console_accounts(self) -> Any:
        """The console accounts store, or ``None``, built once (ADR-0023 §1).

        ``None`` whenever there is no run-state database — the same condition
        as `corrections` — and the console gate answers 503 naming the fix.
        """
        if self._console is not None:
            return self._console
        execute = getattr(self._runs, "_execute", None)
        if execute is None:
            return None

        from docdoc.runs.console_accounts import PostgresConsoleAccounts

        self._console = PostgresConsoleAccounts(execute=execute)
        return self._console

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
        base = self._registry
        if base is None:
            from docdoc.extraction import SchemaRegistry

            raw = os.environ.get(SCHEMA_PATHS_ENV, "")
            base = SchemaRegistry.from_paths([p for p in raw.split(os.pathsep) if p.strip()])
        store = self.console_schemas()
        if store is None:
            return base
        from docdoc.runs.console_schemas import merged_registry

        return merged_registry(base, store)

    def adapter(self) -> Any:
        if self._adapter is not None:
            return self._adapter
        from docdoc.extraction.adapter_registry import default_adapter

        return default_adapter()

    def parser_registry(self) -> ParserRegistry:
        """The parsers ``POST /v1/ocr`` and ``GET /v1/parsers`` choose from.

        Mirrors ``registry()``: a deployment given one explicitly keeps it, and
        one given none falls back to whichever adapters this installation can
        use, including any ``DOCDOC_OCR_ENDPOINTS_FILE`` names (ADR-0021 §3).
        """
        if self._parsers is not None:
            return self._parsers
        from docdoc.ingest.registry import default_registry

        return default_registry()

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


def _configured_cap(name: str, default: int) -> int:
    """A positive cap from the environment, or the default.

    An unreadable or non-positive value falls back rather than refusing to
    start, as `_configured_request_cap` does: a typo in a tuning knob must not
    become an outage -- and must not become "no cap" either, which is why the
    fallback is the default and never zero or unlimited.
    """
    raw = os.environ.get(name, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value > 0 else default


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

    **Validates `DOCDOC_OCR_ENDPOINTS_FILE` first, before anything else is
    built.** `parser_registry()` reads that file again on every call it
    makes (ADR-0021 §3), so a malformed one used to fail silently-to-an-
    operator on whichever request first asked for OCR, as a bare 500 with
    nothing logged at startup to explain it. Raising here instead means a
    bad file keeps this process from ever starting (012 review, Finding 3).
    """
    from docdoc.ingest.registry import validate_configured_endpoints

    validate_configured_endpoints()

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
    app.include_router(console_router())
    # Before the versioned router is irrelevant to routing and relevant to
    # reading: the health routes are not part of the document API and are
    # registered on the application rather than on it (FR-058).
    health.install(app, resolved.readiness())
    _install_error_handler(app)
    _mount_console(app)
    return app


def _require_credential_everywhere_else(app: FastAPI) -> None:
    """FR-059's exemption list, applied to the whole application.

    The router dependency covers `/v1` and resolves a `Principal` for the
    handlers that need one. It cannot cover what is not on that router, and three
    kinds of thing are not: a static **mount**, which inherits no dependency
    because a mount is not a route; FastAPI's `/docs`, `/redoc` and
    `/openapi.json`, which its constructor registers directly; and any path a
    later change adds outside the router.

    All three were open on an authenticated deployment, and the pattern is the
    point — every one of them is a thing nobody put on a list, so the fix is a
    rule that needs no list. FR-059 names exactly two exemptions, so this
    enforces exactly two and refuses everything else.

    **Milestone 11 added the third, and it is deliberate rather than an oversight
    rediscovered.** `/console` serves a shell that must load before a credential
    exists, because that is where the operator types one. See `_CONSOLE_PREFIX`
    for what is exempt and what it is worth to an unauthenticated reader.

    **ADR-0023 §3 moved `/v1/console` off the Bearer check, not out from behind
    a credential.** That route family answers only to a console session, so its
    paths go to `docdoc.api.console.console_gate` *instead of* the branch below:
    a Bearer key is never consulted there, and a missing session is refused
    there. The rest of `/v1` is unchanged, and a console session opens none of
    it.

    **Before routing**, which means an unknown path answers 401 rather than 404
    on an authenticated deployment. That is the better answer: a 404 would say
    which paths exist to someone who cannot use any of them.

    With authentication disabled `principal_for` returns the default tenant for
    everyone and this is a function call per request that changes nothing —
    Milestone 8's behaviour, in which every route was open (FR-088).
    """

    @app.middleware("http")
    async def _credential(request: Request, call_next: Any) -> Response:
        if request.url.path in _UNAUTHENTICATED or _is_console_asset(request.url.path):
            return await call_next(request)  # type: ignore[no-any-return]
        if is_console_api(request.url.path):
            deployment = _deployment_of(request)
            refused = console_gate(
                request, deployment.console_accounts(), idle=deployment.console_idle
            )
            if refused is not None:
                return refused
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


def _mount_console(app: FastAPI) -> None:
    """Serve the operations console, or explain its absence (specs/011 FR-001).

    **Not behind the credential, and that is deliberate.** The viewer's mount was
    gated, and with authentication enabled it *"did not load"*. For the viewer
    that was the honest failure. For the console it would be fatal, because the
    console's premise is that an operator pastes a key into a page that has
    **already loaded** (FR-006) — a browser's top-level navigation carries no
    `Authorization` header and cannot be made to. Gating this shell does not make
    the console strict; it makes it impossible on exactly the deployments it
    exists for.

    What is served is HTML, CSS, and JavaScript. No run, no tenant identifier, no
    credential, and no document is reachable from those bytes, and the console
    issues zero requests before a key is entered. Every `/v1` call it makes after
    that is authenticated exactly as any other client's is. The alternatives —
    a cookie session, a credential in the URL — are rejected in ADR-0019 §4 and
    in the spec's FR-008, and both would have cost more than this exemption does.

    Under ``/console``, which contains none of the fifteen words
    `tests/contract/test_no_review_platform.py` forbids in a path (FR-003). It
    was chosen to be distinct from the viewer's ``/ui``; the viewer is gone
    (ADR-0020) and the path stays, because it is in FR-001, in operators'
    bookmarks, and worth nothing to reclaim.

    **This is now the only mount.** `_mount_ui` sat above it until ADR-0020.
    """
    from docdoc.api.ui import absence_reason, chosen_assets

    source, assets = chosen_assets()

    if assets is None:
        # A deployment without the console is ordinary. A blank page is not:
        # `specs/008` FR-037 forbids it, and the console inherits both the rule
        # and the two different sentences for a missing build and a missing
        # distribution.
        @app.get(_CONSOLE_PREFIX, include_in_schema=False)
        @app.get(f"{_CONSOLE_PREFIX}/{{path:path}}", include_in_schema=False)
        async def _no_console(path: str = "") -> JSONResponse:
            return JSONResponse(
                status_code=501,
                content={
                    "error": {
                        "class": "ConsoleNotInstalled",
                        "message": absence_reason(),
                    }
                },
            )

        return

    from fastapi.staticfiles import StaticFiles
    from starlette.exceptions import HTTPException as StarletteHTTPException

    class _ConsoleFiles(StaticFiles):
        """Falls back to the shell for any miss outside `assets/`.

        A miss under `assets/` stays a 404: answering a missing script with HTML
        would turn a stale deploy into a baffling syntax error.
        """

        async def get_response(self, path, scope):  # type: ignore[no-untyped-def]
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code != 404 or path.startswith("assets/"):
                    raise
                return await super().get_response("index.html", scope)

    logging.getLogger("docdoc.api").info(
        json.dumps({"event": "console.assets", "source": source, "path": str(assets)})
    )

    # The console has client-side routes (`/console/runs/abc`), so a path with no
    # file behind it is the shell's to answer, not a 404 (ADR-0028). `html=True`
    # still serves `index.html` at the directory root, which is what the build
    # emits. It emitted `console.html` until ADR-0020, when the viewer stopped
    # occupying the name — and the plugin that copied one to the other went with it.
    app.mount(
        _CONSOLE_PREFIX,
        _ConsoleFiles(directory=assets, html=True),
        name="console",
    )


def create_app() -> FastAPI:
    """The ASGI factory, for ``uvicorn docdoc.api.app:create_app --factory``."""
    return build_app()


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


def _router() -> APIRouter:
    # The dependency is on the router rather than on each route, so that a route
    # added later is authenticated by default. The alternative — a decorator per
    # endpoint — makes the safe case the one somebody has to remember, and the
    # failure is silent: the new route simply serves everyone.
    #
    # The route families are plain `APIRouter()`s with no prefix and no
    # dependencies, included here, so FastAPI applies this dependency to every
    # route in them: a route added to a family module later is authenticated
    # by default too. Included in this order, with documents split in two,
    # because route order is matching order and this is the order the routes
    # were registered in when they all lived in this function.
    #
    # `/healthz` and `/readyz` are outside this router entirely (FR-058), which
    # is why they are registered on the application in `build_app`.
    router = APIRouter(prefix="/v1", dependencies=[Depends(principal_of)])
    router.include_router(document_routes())
    router.include_router(ocr_routes())
    router.include_router(run_routes())
    router.include_router(evaluation_routes())
    router.include_router(jobs_routes())
    router.include_router(admin_routes())
    return router


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
