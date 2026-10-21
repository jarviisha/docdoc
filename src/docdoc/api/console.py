"""The console's own route family, `/v1/console`, and the gate in front of it.

**A separate route family (ADR-0023 §3).** `/v1/console/*` answers only to the
session cookie; the rest of `/v1` answers only to a Bearer key. Neither opens
the other's doors. The application's credential middleware hands every console
path to `console_gate` *instead of* the Bearer check, so a Bearer key — even an
admin-scoped one — is simply never read here, and the cookie is never read
anywhere else (its `Path=/v1/console` keeps a browser from sending it there).

**Sessions (ADR-0023 §1).** A login opens a session in the
`docdoc.runs.console_accounts` store and sets `docdoc_console`: `HttpOnly`,
`SameSite=Strict`, `Path=/v1/console`, `Secure` over https. The idle bound is
enforced by `resolve` on every gated request. Every non-`GET`/`HEAD` request
must carry `X-Docdoc-Console: 1`, so a cross-site form post cannot forge one
even if `SameSite` failed alone.

This module does not import `docdoc.api.app`: the deployment is read off
`request.app.state`, which keeps the import one-directional.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from docdoc.api.admin import _audit, _erase
from docdoc.api.deps import _iso, _read_capped, _unknown_run
from docdoc.api.documents import _stored_result, _terminal
from docdoc.api.models import (
    ConsoleRunListResponse,
    ConsoleRunSummary,
    ParserListing,
    RunStateResponse,
)
from docdoc.api.ocr import _ocr_document, ocr_unavailable, parser_listing, submit_ocr
from docdoc.api.paging import DEFAULT_RUN_PAGE
from docdoc.api.runs import (
    _finished_at,
    _next_cursor,
    _no_runs,
    _page_request,
    _routing_for,
    _run_erased,
    _summary_fields,
)
from docdoc.artifacts.paths import root_tenant
from docdoc.runs import identity
from docdoc.runs.model import Priority
from docdoc.runs.principal import TENANT_PATTERN

if TYPE_CHECKING:
    from datetime import timedelta

__all__ = [
    "CONSOLE_API_PREFIX",
    "CONSOLE_COOKIE",
    "CONSOLE_HEADER",
    "Operator",
    "console_gate",
    "console_router",
    "is_console_api",
    "operator_of",
]

CONSOLE_API_PREFIX = "/v1/console"
CONSOLE_COOKIE = "docdoc_console"
CONSOLE_HEADER = "X-Docdoc-Console"

_SAFE_METHODS = frozenset({"GET", "HEAD"})
_SESSION_PATH = f"{CONSOLE_API_PREFIX}/session"


def _refusal(status: int, error_class: str, message: str) -> JSONResponse:
    return JSONResponse(
        status_code=status, content={"error": {"class": error_class, "message": message}}
    )


def is_console_api(path: str) -> bool:
    """Whether a path belongs to the console's route family.

    The bare prefix or the prefix plus a slash, so a later `/v1/consoles`
    route is not swept in by accident (the same care `_is_console_asset`
    takes with the shell).
    """
    return path == CONSOLE_API_PREFIX or path.startswith(f"{CONSOLE_API_PREFIX}/")


def console_gate(request: Request, accounts: Any, *, idle: timedelta) -> JSONResponse | None:
    """The console's credential check, in ADR-0023's order; `None` lets it through.

    1. No accounts store means no run-state database: 503, naming the fix.
    2. A write without `X-Docdoc-Console: 1`: 403, before the session is even
       looked at (ADR-0023 §1 — independent of whether the session is valid).
    3. `POST /v1/console/session` is the way in, so it needs no session.
    4. Anything else needs a live session cookie: 401, with no
       `WWW-Authenticate` — this is not a Bearer challenge and a Bearer key
       would not answer it.

    On success sets `request.state.operator` to the signed-in username.
    `idle` is the deployment's bound, read once at startup rather than per
    request (`_Deployment.console_idle`).
    """
    if accounts is None:
        return _refusal(
            503,
            "ConsoleUnavailable",
            "the console needs a run-state database; set DOCDOC_RUN_DATABASE_URL "
            "and run docdoc migrate",
        )
    if request.method not in _SAFE_METHODS and request.headers.get(CONSOLE_HEADER) != "1":
        return _refusal(
            403,
            "ConsoleHeaderMissing",
            f"a console request that changes anything must carry {CONSOLE_HEADER}: 1",
        )
    if request.method == "POST" and request.url.path == _SESSION_PATH:
        return None
    token = request.cookies.get(CONSOLE_COOKIE)
    username = accounts.resolve(token, now=identity.now(), idle=idle) if token else None
    if username is None:
        return _refusal(401, "ConsoleSessionRequired", "sign in to the console")
    request.state.operator = username
    return None


async def operator_of(request: Request) -> str:
    """The signed-in operator, as `console_gate` resolved it.

    The gate has already refused anything without a session; this is the
    route-level half, so a console route cannot be reached by a path the gate
    somehow did not see.
    """
    operator = getattr(request.state, "operator", None)
    if operator is None:
        raise HTTPException(
            status_code=401,
            detail={
                "error": {"class": "ConsoleSessionRequired", "message": "sign in to the console"}
            },
        )
    return str(operator)


Operator = Annotated[str, Depends(operator_of)]


class _Login(BaseModel):
    #: Bounded so an unauthenticated caller cannot hand scrypt an arbitrarily
    #: large input; nobody's password or username is near either bound.
    username: str = Field(max_length=256)
    password: str = Field(max_length=1024)


class _ConsoleCredentialRequest(BaseModel):
    """`POST /v1/console/credentials`'s body. No `scopes` (ADR-0023 §2).

    `scopes` is typed `Any` and defaulted so the model always parses; the
    refusal in `issue_credential` is on the key being *present* at all
    (`model_fields_set`), not on what it holds. An admin-scoped key stays
    CLI-only, and a console session is one browser tab away from a key an
    operator did not mean to mint, so there is no "which scopes" question to
    ask here.
    """

    tenant_id: str = Field(max_length=256)
    label: str | None = Field(default=None, max_length=256)
    scopes: Any = None


def _accounts(request: Request) -> Any:
    return request.app.state.deployment.console_accounts()


def _log_erasure(operator: str, *, tenant_id: str, blob_id: str | None, status: int) -> None:
    """One structured event per console erasure attempt, attributed.

    `_erase` and `retention.erase` are shared with the admin routes and log no
    actor there (the admin routes have no operator identity to name), so the
    attribution this global constraint requires is the console's own to add.
    Logged for a refusal too -- a refused destructive attempt is still an
    attempt worth being able to find later -- in the same "the message is the
    JSON" style `console.assets` and `runs.driver_missing` already use on this
    logger.
    """
    logging.getLogger("docdoc.api").info(
        json.dumps(
            {
                "event": "console.erasure",
                "actor": f"console:{operator}",
                "tenant_id": tenant_id,
                "blob_id": blob_id,
                "status": status,
            }
        )
    )


def _log_cancel(operator: str, *, tenant_id: str, run_id: str, status: int) -> None:
    """One structured event per console cancel, attributed as `_log_erasure` is.

    `DELETE /v1/runs/{id}` names no actor (its caller is the tenant itself); a
    console cancel acts on someone else's run, so it says who did.
    """
    logging.getLogger("docdoc.api").info(
        json.dumps(
            {
                "event": "console.cancel",
                "actor": f"console:{operator}",
                "tenant_id": tenant_id,
                "run_id": run_id,
                "status": status,
            }
        )
    )


def _log_ocr_submit(operator: str, *, tenant_id: str, run_id: str, status: int) -> None:
    """One structured event per accepted console OCR submission, as `_log_cancel` does.

    `POST /v1/ocr`'s caller is the tenant itself; a console submission is made
    on a tenant's behalf, so it says who made it.
    """
    logging.getLogger("docdoc.api").info(
        json.dumps(
            {
                "event": "console.ocr_submit",
                "actor": f"console:{operator}",
                "tenant_id": tenant_id,
                "run_id": run_id,
                "status": status,
            }
        )
    )


def _summary(run: Any) -> ConsoleRunSummary:
    """A run as a console row: the tenant row, plus its tenant and four monitoring fields."""
    return ConsoleRunSummary(
        **_summary_fields(run),
        tenant_id=run.tenant_id,
        started_at=_iso(run.started_at),
        ocr_parser=run.ocr_parser,
        error_class=run.error_class,
        error_reason=run.error_reason,
    )


def _detail(run: Any, owner: str) -> dict[str, Any]:
    """The console's detail body: the tenant's, plus `tenant_id` and `finished_at`."""
    return _owned(RunStateResponse(**run.dump_public()).model_dump(mode="json"), owner)


def _owned(body: dict[str, Any], owner: str) -> dict[str, Any]:
    """A tenant's run body (`RunStateResponse` as JSON) as the console's detail.

    `finished_at` is the list row's rule (`_finished_at`), so detail and row
    agree. Not on `RunStateResponse`: the tenant contract stays as it is.
    """
    updated_at = datetime.fromisoformat(body["updated_at"])
    return {**body, "tenant_id": owner, "finished_at": _finished_at(body["status"], updated_at)}


#: The Overview's window for terminal counts, and the length of each list.
_OVERVIEW_WINDOW_MINUTES = 60
_OVERVIEW_LIST = 10


def _owner(request: Request, run_id: str) -> tuple[JSONResponse | None, str, Any]:
    """`(refusal, owner, identity)` for a run named by identity alone.

    `owner_of` names the tenant, and every read after it is the tenant-scoped
    one a tenant's own route makes, so the console is answered about a run
    exactly as its owner is (ADR-0023 §3). Unknown and malformed are one 404.
    """
    from uuid import UUID

    deployment = request.app.state.deployment
    if not deployment.has_runs:
        return _no_runs(), "", None
    try:
        identity = UUID(run_id)
    except ValueError:
        return _unknown_run(run_id), "", None
    owner = deployment.runs().owner_of(identity)
    if owner is None:
        return _unknown_run(run_id), "", None
    return None, owner, identity


def _run(request: Request, run_id: str) -> tuple[JSONResponse | None, str, Any]:
    """`(refusal, owner, run)`: `_owner`, then the run, or its owner's 410."""

    refusal, owner, identity = _owner(request, run_id)
    if refusal is not None:
        return refusal, owner, None
    queue = request.app.state.deployment.runs()
    run = queue.get(identity, owner)
    if run is None:
        stone = queue.tombstone(identity, owner)
        return (_unknown_run(run_id) if stone is None else _run_erased(stone)), owner, None
    return None, owner, run


def console_router() -> APIRouter:
    """`/v1/console`: sessions (ADR-0023 §1) and cross-tenant run reads (§3)."""
    router = APIRouter(prefix=CONSOLE_API_PREFIX)

    # Plain `def`, not `async def`: `login` runs scrypt and a blocking database
    # call, and FastAPI runs a sync handler in its threadpool. On the event loop
    # an unauthenticated caller posting logins would stall every other request.
    @router.post("/session")
    def sign_in(request: Request, body: _Login) -> Any:
        """Open a session, or the one refusal every failure shares.

        Wrong username, wrong password, locked, and disabled all get the same
        body (ADR-0023 §1); the store counts the failure and decides the lock.
        It also lower-cases and trims the username, so the answer below is the
        normalised name.
        """
        from docdoc.runs.console_accounts import normalise_username

        token = identity.new_console_token()
        if not _accounts(request).login(
            body.username, body.password, token=token, now=identity.now()
        ):
            return _refusal(
                401,
                "LoginRefused",
                "wrong username or password, or the account is locked",
            )
        response = JSONResponse({"username": normalise_username(body.username)})
        response.set_cookie(
            CONSOLE_COOKIE,
            token,
            httponly=True,
            samesite="strict",
            path=CONSOLE_API_PREFIX,
            secure=request.url.scheme == "https",
        )
        return response

    @router.get("/session")
    async def who(operator: Operator) -> Any:
        return {"username": operator}

    @router.delete("/session", status_code=204)
    def sign_out(request: Request, operator: Operator) -> Response:
        _accounts(request).logout(request.cookies[CONSOLE_COOKIE])
        response = Response(status_code=204)
        response.delete_cookie(
            CONSOLE_COOKIE,
            path=CONSOLE_API_PREFIX,
            httponly=True,
            samesite="strict",
            secure=request.url.scheme == "https",
        )
        return response

    # The run reads below are plain `def` for the reason `sign_in` is: every one
    # makes blocking database and store reads.

    @router.get("/runs", response_model=ConsoleRunListResponse)
    def list_runs(
        request: Request,
        operator: Operator,
        tenant: str | None = None,
        status: str | None = None,
        kind: str | None = None,
        limit: int = DEFAULT_RUN_PAGE,
        cursor: str | None = None,
    ) -> Any:
        """Every tenant's runs, or one tenant's, newest first; each row names its tenant.

        `GET /v1/runs` with the tenant predicate made optional. A cursor is
        bound to the filter that produced it: to `tenant`, or to `"*"` when
        there is none, which no tenant identifier can match. `kind` is not in
        the binding, as `status` is not: changing either restarts paging.
        """
        from docdoc.runs.model import RunKind

        if tenant is not None and not TENANT_PATTERN.match(tenant):
            return JSONResponse(status_code=422, content={"error": "invalid_tenant_id"})
        kinds = sorted(member.value for member in RunKind)
        if kind is not None and kind not in kinds:
            return _refusal(422, "InvalidRunKind", "kind must be one of " + ", ".join(kinds))
        deployment = request.app.state.deployment
        if not deployment.has_runs:
            return _no_runs()
        bound = tenant or "*"
        refusal, before = _page_request(status, limit, cursor, cursor_tenant=bound)
        if refusal is not None:
            return refusal
        runs = deployment.runs().page_all_runs(
            tenant_id=tenant, status=status, kind=kind, before=before, limit=limit
        )
        return ConsoleRunListResponse(
            runs=tuple(_summary(run) for run in runs),
            next_cursor=_next_cursor(runs, limit, cursor_tenant=bound),
        )

    @router.get("/overview")
    def overview(request: Request, operator: Operator) -> Any:
        """Counts by kind and status, the newest failures, and what is running.

        `queued` and `running` count at any age -- a run stuck there for hours
        is exactly what an operator needs to see. The terminal statuses count
        only within the window, so the tiles say what just happened rather
        than everything that ever did. `running` lists the longest-running
        first, so a stuck run stays on top rather than sinking.
        """
        from datetime import timedelta

        from docdoc.runs.model import RunStatus

        deployment = request.app.state.deployment
        if not deployment.has_runs:
            return _no_runs()
        queue = deployment.runs()
        since = identity.now() - timedelta(minutes=_OVERVIEW_WINDOW_MINUTES)
        return {
            "window_minutes": _OVERVIEW_WINDOW_MINUTES,
            "counts": [
                {"kind": kind, "status": status, "count": count}
                for kind, status, count in queue.status_counts(since=since)
            ],
            "recent_failures": [
                _summary(run).model_dump(mode="json")
                for run in queue.latest(status=RunStatus.FAILED.value, limit=_OVERVIEW_LIST)
            ],
            "running": [
                _summary(run).model_dump(mode="json")
                for run in queue.longest_running(limit=_OVERVIEW_LIST)
            ],
        }

    @router.get("/runs/{run_id}")
    def run_state(request: Request, run_id: str, operator: Operator) -> Any:
        """`GET /v1/runs/{run_id}`'s body, plus the run's `tenant_id`."""

        refusal, owner, run = _run(request, run_id)
        if refusal is not None:
            return refusal
        body = _detail(run, owner)
        decision = _routing_for(request, run)
        if decision is not None:
            body["routing"] = decision.model_dump(mode="json")
        return JSONResponse(content=body)

    @router.delete("/runs/{run_id}")
    def cancel_run(request: Request, run_id: str, operator: Operator) -> Any:
        """`DELETE /v1/runs/{run_id}`, under the run's owner, plus its `tenant_id`.

        The same three answers: 200 with the run (a running run still reads
        `running` until its worker reaches a stage boundary, FR-029), 409 for a
        finished one, and the one 404. Every attempt on a known run is logged
        with its actor, a refusal included, as `_log_erasure` does.
        """
        from docdoc.runs.errors import RunNotCancellableError, RunNotFoundError

        refusal, owner, run_identity = _owner(request, run_id)
        if refusal is not None:
            return refusal
        response: JSONResponse
        try:
            run = request.app.state.deployment.runs().cancel(
                run_identity, owner, now=identity.now()
            )
        except RunNotFoundError:
            response = _unknown_run(run_id)
        except RunNotCancellableError as exc:
            response = JSONResponse(
                status_code=409,
                content={
                    "error": {
                        "class": type(exc).__name__,
                        "message": str(exc),
                        "detail": {"status": exc.state},
                    }
                },
            )
        else:
            response = JSONResponse(content=_detail(run, owner))
        _log_cancel(operator, tenant_id=owner, run_id=run_id, status=response.status_code)
        return response

    @router.get("/runs/{run_id}/delivery")
    def run_delivery(request: Request, run_id: str, operator: Operator) -> Any:
        """`GET /v1/runs/{run_id}/delivery`, looked up under the run's owner."""

        refusal, owner, identity = _owner(request, run_id)
        if refusal is not None:
            return refusal
        deliverer = request.app.state.deployment.deliverer()
        if deliverer is None:
            return _unknown_run(run_id)
        delivery = deliverer.for_run(run_id=identity, tenant_id=owner)
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

    @router.get("/runs/{run_id}/result")
    def run_result(request: Request, run_id: str, operator: Operator) -> Any:
        """A succeeded run's result, whichever kind it is (ADR-0023 §4).

        An OCR run's `Document`, as `GET /v1/runs/{run_id}/result` serves it; an
        extraction run's stored result, as `GET /v1/jobs/{id}/result` does. One
        route because the console holds a run, not a `processing_id`.
        """
        from docdoc.runs.model import RunKind, RunStatus

        refusal, owner, run = _run(request, run_id)
        if refusal is not None:
            return refusal
        if run.kind is RunKind.OCR:
            return _ocr_document(request, owner, run, with_angles=True)
        if run.status is not RunStatus.SUCCEEDED or run.processing_id is None:
            return JSONResponse(
                status_code=409,
                content={"error": "run_not_succeeded", "status": str(run.status)},
            )
        store, _ = request.app.state.deployment.stores_for(owner)
        validation = _terminal(store, run.processing_id)
        if validation is None:
            # The run row outlived its content, as `_ocr_document` says of its own.
            return JSONResponse(status_code=410, content={"error": "result_expired"})
        return _stored_result(store, run.processing_id, validation)

    @router.get("/runs/{run_id}/pages/{page}")
    def run_page(request: Request, run_id: str, page: int, operator: Operator) -> Any:
        """Page `page` (0-based) of an OCR run's source document, as an image (019).

        PNG and JPEG are served as stored, page 0 only; a PDF page is rendered
        to PNG, longer edge at most 2000 px. An out-of-range page and a missing
        blob are the one 404 an unknown run gets. Plain `def`: rendering blocks.
        """
        from docdoc.api.pages import page_image
        from docdoc.runs.model import RunKind

        refusal, owner, run = _run(request, run_id)
        if refusal is not None:
            return refusal
        if run.kind is not RunKind.OCR:
            return JSONResponse(
                status_code=409,
                content={
                    "error": "no_page_images",
                    "detail": "page images are served for OCR runs only",
                },
            )
        _, blobs = request.app.state.deployment.stores_for(owner)
        data = None if blobs is None else blobs.get(run.blob_id)
        if data is None:
            return _unknown_run(run_id)
        try:
            image, media_type = page_image(data, page)
        except (IndexError, ValueError):
            return _unknown_run(run_id)
        except RuntimeError as exc:
            if exc.args != ("pypdfium2",):
                raise
            return _refusal(
                503,
                "PageRenderingUnavailable",
                "rendering PDF pages needs the docdoc[ocr] extra (pypdfium2)",
            )
        return Response(
            content=image,
            media_type=media_type,
            headers={"Cache-Control": "private, max-age=300"},
        )

    # The three credential routes and the two erasure routes below are plain
    # `def`, for the reason every other handler in this file is: each makes a
    # blocking database call.

    @router.get("/credentials")
    def list_credentials(request: Request, operator: Operator, tenant: str) -> Any:
        """`GET /v1/admin/credentials`'s body, over the console's own query name."""

        if not TENANT_PATTERN.match(tenant):
            return JSONResponse(status_code=422, content={"error": "invalid_tenant_id"})
        deployment = request.app.state.deployment
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
                for credential in deployment.keys.list_for(tenant)
            ]
        }

    @router.post("/credentials", status_code=201)
    def issue_credential(
        request: Request, operator: Operator, body: _ConsoleCredentialRequest
    ) -> Any:
        """Issue a tenant credential, as `POST /v1/admin/credentials` does.

        **No `scopes`, ever** (ADR-0023 §2): any `scopes` key in the body is
        refused outright, which is narrower than the admin route's "any scope
        but `admin`" -- the admin route is reached with a key that already
        carries the scope; this one is reached with a browser session.
        """

        if "scopes" in body.model_fields_set:
            return JSONResponse(status_code=422, content={"error": "scopes_not_accepted"})
        tenant_id = body.tenant_id.strip()
        if not TENANT_PATTERN.match(tenant_id):
            return JSONResponse(status_code=422, content={"error": "invalid_tenant_id"})

        label = (body.label or "").strip() or None
        deployment = request.app.state.deployment
        secret, credential = deployment.keys.issue(
            tenant_id=tenant_id, scopes=frozenset(), label=label, now=identity.now()
        )
        _audit(actor=f"console:{operator}", operation="issue", credential=credential)
        return JSONResponse(
            status_code=201,
            content={
                "credential_id": str(credential.credential_id),
                "tenant_id": credential.tenant_id,
                "key": secret,
                "created_at": credential.created_at.isoformat() if credential.created_at else None,
            },
        )

    @router.delete("/credentials/{credential_id}", status_code=204)
    def revoke_credential(request: Request, credential_id: str, operator: Operator) -> Response:
        """Revoke, idempotently, as `DELETE /v1/admin/credentials/{id}` does."""
        from uuid import UUID

        try:
            identity_ = UUID(credential_id)
        except ValueError:
            return JSONResponse(status_code=404, content={"error": "not_found"})

        deployment = request.app.state.deployment
        deployment.keys.revoke(identity_, now=identity.now())
        _audit(actor=f"console:{operator}", operation="revoke", credential_id=identity_)
        return Response(status_code=204)

    @router.get("/tenants")
    def list_tenants(request: Request, operator: Operator) -> Any:
        """Every tenant docdoc knows of, sorted: the union of four sources.

        The root tenant, the key file's, the credentials table's, and every
        tenant with a run. A source that is not configured adds nothing.
        """
        deployment = request.app.state.deployment
        tenants = {root_tenant(), *deployment.keys.tenant_ids()}
        if deployment.has_runs:
            tenants.update(deployment.runs().tenant_ids())
        return {"tenants": sorted(tenants)}

    @router.get("/parsers", response_model=ParserListing, response_model_exclude_none=True)
    def list_parsers(request: Request, operator: Operator) -> Any:
        """`GET /v1/parsers`'s body, behind a session. Blocking: it probes."""
        return parser_listing(request.app.state.deployment)

    @router.post("/tenants/{tenant_id}/ocr", status_code=202)
    async def submit_tenant_ocr(
        request: Request,
        tenant_id: str,
        operator: Operator,
        parser: str | None = None,
        language: str | None = None,
    ) -> Any:
        """`POST /v1/ocr` for a named tenant, answering with the console's run detail.

        The same `submit_ocr` the tenant route calls, so the caps, validation,
        blob storage and idempotency are the tenant's own. `async` because the
        body is read with `await`; `submit_ocr` moves the blocking work to the
        threadpool.
        """
        if not TENANT_PATTERN.match(tenant_id):
            return JSONResponse(status_code=422, content={"error": "invalid_tenant_id"})
        refusal = ocr_unavailable(request)
        if refusal is not None:
            return refusal
        data = await _read_capped(request, request.app.state.deployment.max_request_bytes)
        response = await submit_ocr(
            request,
            tenant_id=tenant_id,
            data=data,
            parser=parser,
            language=language,
            callback_id=None,
            # An operator submitting here is waiting at the screen (019).
            priority=Priority.URGENT,
        )
        if response.status_code not in (200, 202):
            return response
        # Built from the answer, not read back: the run exists now, and a read
        # that failed would turn it into an error -- and the caller's retry
        # into a second run.
        body = _owned(json.loads(bytes(response.body)), tenant_id)
        _log_ocr_submit(
            operator, tenant_id=tenant_id, run_id=body["run_id"], status=response.status_code
        )
        return JSONResponse(status_code=response.status_code, content=body)

    @router.delete("/tenants/{tenant_id}")
    def erase_tenant(request: Request, tenant_id: str, operator: Operator) -> Any:
        """Erase a tenant's data, as `DELETE /v1/admin/tenants/{id}` does.

        The default tenant is refused here on the same terms (ADR-0015 §5): its
        namespace is the store root, so erasing it would remove content no run
        ever produced.
        """

        if not TENANT_PATTERN.match(tenant_id):
            return JSONResponse(status_code=422, content={"error": "invalid_tenant_id"})

        deployment = request.app.state.deployment
        if not deployment.has_runs:
            return _unknown_run(tenant_id)

        if tenant_id == root_tenant():
            _log_erasure(operator, tenant_id=tenant_id, blob_id=None, status=409)
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

        response = _erase(request, tenant_id=tenant_id, blob_id=None)
        _log_erasure(operator, tenant_id=tenant_id, blob_id=None, status=response.status_code)
        return response

    @router.delete("/tenants/{tenant_id}/documents/{blob_id}")
    def erase_document(request: Request, tenant_id: str, blob_id: str, operator: Operator) -> Any:
        """Erase one document in a named tenant.

        `DELETE /v1/admin/documents/{blob_id}` is scoped to the administrator's
        own tenant; the console names the tenant in the path instead, since an
        operator's session carries no tenant of its own (ADR-0023 §3).
        """

        if not TENANT_PATTERN.match(tenant_id):
            return JSONResponse(status_code=422, content={"error": "invalid_tenant_id"})

        response = _erase(request, tenant_id=tenant_id, blob_id=blob_id)
        _log_erasure(operator, tenant_id=tenant_id, blob_id=blob_id, status=response.status_code)
        return response

    return router
