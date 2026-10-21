"""`/v1/ocr`, `/v1/runs/{run_id}/result`, and `/v1/parsers` (ADR-0022)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from docdoc.api.deps import (
    Caller,
    _default_limits,
    _deployment_of,
    _no_store_configured,
    _read_capped,
    _unknown_run,
)
from docdoc.api.models import ParserChoice, ParserListing, RunStateResponse
from docdoc.api.runs import _UNKNOWN_CALLBACK, _requested_callback, _run_erased, _RunSpec
from docdoc.api.settings import RUN_DATABASE_URL_ENV
from docdoc.runs.model import Priority

#: How long a refused OCR submission is told to wait (ADR-0022). Short, and
#: for the reason `concurrent_runs` gives: what frees a slot is a run finishing,
#: and docdoc cannot say when.
_OCR_RETRY_AFTER_SECONDS = 30


def routes() -> APIRouter:
    """The OCR submission, its result, and the parser listing."""
    router = APIRouter()

    @router.post("/ocr", status_code=202)
    async def ocr(
        request: Request,
        principal: Caller,
        parser: str | None = None,
        language: str | None = None,
        callback_id: str | None = None,
    ) -> Any:
        """Accept a document for OCR and return before any OCR happens (ADR-0022).

        The document becomes an OCR run in the same queue extraction runs use,
        executed by a worker claiming `--kind ocr`. Poll `GET /v1/runs/{run_id}`,
        or register a callback, and read the `Document` from
        `GET /v1/runs/{run_id}/result` once the run has succeeded.

        ``parser`` names a configured parser by id (ADR-0021 §1); omitted, the
        usual capability-based selection chooses one. ``language`` becomes
        ``options={"language": ...}`` and **asserts** rather than selects: the
        chosen endpoint must declare it. Both are checked here, before a slot is
        taken, and either refused is a 422 -- never a silent substitution.

        **Bounded twice, and refused honestly.** Over this tenant's cap: 429.
        Over the deployment's: 503, "overloaded, send again later". Both carry
        `Retry-After`; neither leaves a run or a blob behind. The synchronous
        OCR this route used to perform is gone: every OCR goes through the
        queue, so nothing bypasses either cap.
        """
        refusal = ocr_unavailable(request)
        if refusal is not None:
            return refusal
        # The body still has to be read with `await` -- `_read_capped` drives
        # `request.stream()`, an async generator.
        data = await _read_capped(request, _deployment_of(request).max_request_bytes)
        return await submit_ocr(
            request,
            tenant_id=principal.tenant_id,
            data=data,
            parser=parser,
            language=language,
            callback_id=callback_id,
        )

    @router.get("/runs/{run_id}/result")
    async def run_result(request: Request, run_id: str, principal: Caller) -> Any:
        """An OCR run's `Document`, once it has succeeded (ADR-0022).

        Scoped to the caller's tenant exactly as `GET /v1/runs/{run_id}` is:
        unknown, malformed, and another tenant's are one 404. An extraction
        run's result is served by `GET /v1/jobs/{processing_id}/result`, as
        before, and this route says so rather than serving a second
        representation of it.
        """
        from uuid import UUID

        deployment = _deployment_of(request)
        if not deployment.has_runs:
            return _unknown_run(run_id)
        try:
            identity = UUID(run_id)
        except ValueError:
            return _unknown_run(run_id)

        run = deployment.runs().get(identity, principal.tenant_id)
        if run is None:
            stone = deployment.runs().tombstone(identity, principal.tenant_id)
            if stone is None:
                return _unknown_run(run_id)
            return _run_erased(stone)

        return _ocr_document(request, principal.tenant_id, run)

    # A plain `def`, not `async def`: FastAPI runs a synchronous handler in its
    # worker thread pool, so the blocking health probes below -- one urlopen
    # per configured parser, each up to its own timeout -- cannot stall the
    # event loop the way they would inside a coroutine every other request on
    # this process is waiting on.
    @router.get("/parsers", response_model=ParserListing, response_model_exclude_none=True)
    def parsers(request: Request) -> Any:
        """The parsers this deployment has configured, in priority order.

        Never a URL, never a header (Review Focus 3's HTTP half) -- a
        `ParserChoice` has no field that could carry either. `unavailable_reason`,
        `languages`, and `healthy` are absent rather than null when they do not
        apply, which `response_model_exclude_none` is what actually omits them.
        """
        return parser_listing(_deployment_of(request))

    return router


def ocr_unavailable(request: Request) -> JSONResponse | None:
    """Why this deployment accepts no OCR at all, or `None` (ADR-0022).

    Checked before the body is read, by every route that submits OCR.
    """
    deployment = _deployment_of(request)
    if not deployment.has_runs:
        # Not retryable, exactly as `submit_run` says: nothing changes until
        # an operator configures a database.
        return JSONResponse(
            status_code=503,
            content={
                "error": {
                    "class": "RunStateUnavailableError",
                    "message": (
                        "this deployment accepts no OCR: OCR runs through the run "
                        f"queue, so set {RUN_DATABASE_URL_ENV} and apply `docdoc migrate`"
                    ),
                }
            },
        )
    if not deployment.has_store:
        raise _no_store_configured()
    return None


async def submit_ocr(
    request: Request,
    *,
    tenant_id: str,
    data: bytes,
    parser: str | None,
    language: str | None,
    callback_id: str | None,
    priority: Priority = Priority.ORDINARY,
) -> JSONResponse:
    """Queue `data` as an OCR run for `tenant_id`, or refuse it (ADR-0022).

    The one OCR submission path: `POST /v1/ocr` and the console's
    `POST /v1/console/tenants/{tenant_id}/ocr` both call it, after
    `ocr_unavailable`, so caps, validation, blob storage and idempotency are
    never copied. `x-request-id` and `idempotency-key` are read off
    `request.headers`. `priority` is the caller's to grant: `/v1/ocr` leaves it
    ordinary, the console's operator-driven submissions are urgent (019).
    """
    from fastapi.concurrency import run_in_threadpool

    from docdoc.ingest.parse import plan_parse
    from docdoc.ingest.source import SourceFile
    from docdoc.runs.errors import RunStateUnavailableError
    from docdoc.runs.identity import configured_ocr_retention, deadline, new_run_id, now
    from docdoc.runs.model import RunKind

    deployment = _deployment_of(request)

    # **Everything below blocks**, and none of it may run on the event loop:
    # planning the parse reads the endpoints file and a PDF's text layer,
    # the caps are two database round trips, and the blob is an object
    # store PUT of the whole body. Inline, each submission would stall every
    # other request this process serves, health probes included -- the
    # reason the synchronous route ran its parse in the threadpool too.
    def _accept() -> Any:
        file = SourceFile.from_bytes(data, limits=deployment.limits)
        file.check_limits(deployment.limits or _default_limits())

        # **Everything that can be refused without OCR is refused here**, before
        # a slot is counted or a blob is written: an unknown or incapable parser
        # and an undeclared language are both a 422 now, not a failed run later.
        options = {} if language is None else {"language": language}
        plan = plan_parse(
            file,
            options=options,
            registry=deployment.parser_registry(),
            parser=parser,
            limits=deployment.limits,
        )
        check_options = getattr(plan.parser, "check_options", None)
        if check_options is not None:
            check_options(options, blob_id=file.blob_id)

        requested_callback = _requested_callback(deployment, callback_id, tenant_id=tenant_id)
        if requested_callback is _UNKNOWN_CALLBACK:
            return JSONResponse(
                status_code=404,
                content={"error": {"class": "UnknownCallback", "message": "no such callback here"}},
            )

        # ponytail: count-then-insert, so N concurrent submissions can overshoot
        # a cap by up to N-1; add an advisory lock if that margin ever matters.
        queue = deployment.runs()
        try:
            total = queue.count_active(kind=RunKind.OCR)
            if total >= deployment.ocr_queue_total:
                # The system's cap, not this tenant's: docdoc is full.
                return JSONResponse(
                    status_code=503,
                    headers={"Retry-After": str(_OCR_RETRY_AFTER_SECONDS)},
                    content={
                        "error": "system_overloaded",
                        "limit": "ocr_queue_total",
                        "retry_after_seconds": _OCR_RETRY_AFTER_SECONDS,
                        "message": "the system is overloaded; please send again later",
                    },
                )
            cap = deployment.ocr_queue_for(tenant_id)
            mine = queue.count_active(kind=RunKind.OCR, tenant_id=tenant_id)
            if mine >= cap:
                # The same shape every other limit refusal has.
                return JSONResponse(
                    status_code=429,
                    headers={"Retry-After": str(_OCR_RETRY_AFTER_SECONDS)},
                    content={
                        "error": "limit_exceeded",
                        "limit": "ocr_queue",
                        "observed": mine,
                        "allowed": cap,
                        "retry_after_seconds": _OCR_RETRY_AFTER_SECONDS,
                    },
                )

            _, blobs = deployment.stores_for(tenant_id)
            assert blobs is not None
            blob_id = blobs.put(data)
            started = now()
            allocated = new_run_id()
            run = queue.submit(
                _RunSpec(
                    tenant_id=tenant_id,
                    blob_id=blob_id,
                    schema_identity=None,
                    request_id=request.headers.get("x-request-id"),
                    idempotency_key=request.headers.get("idempotency-key"),
                    callback_id=requested_callback,
                    kind=str(RunKind.OCR),
                    ocr_parser=parser,
                    ocr_language=language,
                    priority=int(priority),
                ),
                run_id=allocated,
                now=started,
                # Re-anchored to the run's finish by `finish` (ADR-0022).
                expires_at=deadline(started, configured_ocr_retention()),
            )
        except RunStateUnavailableError as exc:
            # Transient: the database is configured and unreachable. A count
            # that failed is never read as zero -- that would admit everything.
            return JSONResponse(
                status_code=503,
                content={"error": {"class": type(exc).__name__, "message": str(exc)}},
                headers={"Retry-After": "1"},
            )

        return JSONResponse(
            status_code=200 if run.run_id != allocated else 202,
            content=RunStateResponse(**run.dump_public()).model_dump(mode="json"),
        )

    return await run_in_threadpool(_accept)


def parser_listing(deployment: Any) -> ParserListing:
    """`GET /v1/parsers`'s body, which the console serves too. Blocking: it probes."""
    choices = []
    for entry in deployment.parser_registry().candidates_all():
        probe = getattr(entry.parser, "probe", None)
        languages = tuple(getattr(entry.parser, "languages", ()) or ())
        choices.append(
            ParserChoice(
                id=entry.id,
                available=entry.available,
                unavailable_reason=entry.unavailable_reason,
                capabilities=entry.capabilities.to_kernel().model_dump(mode="json"),
                media_types=tuple(sorted(entry.capabilities.media_types)),
                languages=languages or None,
                healthy=probe() if callable(probe) else None,
            )
        )
    return ParserListing(parsers=tuple(choices))


def _ocr_document(request: Request, tenant_id: str, run: Any) -> Any:
    """An OCR run's `Document` from `tenant_id`'s store, or why not (ADR-0022).

    Shared by `GET /v1/runs/{run_id}/result` and its console twin, which reach
    the run differently and must answer about it identically.
    """
    from docdoc.kernel import Document
    from docdoc.pipeline.stages import Stage, spec_for
    from docdoc.runs.model import RunKind, RunStatus

    if run.kind is not RunKind.OCR:
        return JSONResponse(
            status_code=409,
            content={
                "error": "not_an_ocr_run",
                "detail": "use GET /v1/jobs/{processing_id}/result",
            },
        )
    if run.status is not RunStatus.SUCCEEDED or run.processing_id is None:
        return JSONResponse(
            status_code=409,
            content={"error": "run_not_succeeded", "status": str(run.status)},
        )

    store, _ = _deployment_of(request).stores_for(tenant_id)
    document = store.get(
        run.processing_id,
        model=Document,
        artifact_format_version=spec_for(Stage.PARSE).artifact_format_version,
    )
    if document is None:
        # The run row outlived its content -- a sweep interrupted between
        # its steps finishes on the next tick. Gone either way.
        return JSONResponse(status_code=410, content={"error": "result_expired"})
    return document.model_dump(mode="json")
