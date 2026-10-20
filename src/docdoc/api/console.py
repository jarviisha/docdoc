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

from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from docdoc.api.models import ConsoleRunListResponse, ConsoleRunSummary, RunStateResponse
from docdoc.api.paging import DEFAULT_RUN_PAGE
from docdoc.runs import identity

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


def _accounts(request: Request) -> Any:
    return request.app.state.deployment.console_accounts()


def _owner(request: Request, run_id: str) -> tuple[JSONResponse | None, str, Any]:
    """`(refusal, owner, identity)` for a run named by identity alone.

    `owner_of` names the tenant, and every read after it is the tenant-scoped
    one a tenant's own route makes, so the console is answered about a run
    exactly as its owner is (ADR-0023 §3). Unknown and malformed are one 404.
    """
    from uuid import UUID

    from docdoc.api.app import _no_runs, _unknown_run

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
    from docdoc.api.app import _run_erased, _unknown_run

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
        limit: int = DEFAULT_RUN_PAGE,
        cursor: str | None = None,
    ) -> Any:
        """Every tenant's runs, or one tenant's, newest first; each row names its tenant.

        `GET /v1/runs` with the tenant predicate made optional. A cursor is
        bound to the filter that produced it: to `tenant`, or to `"*"` when
        there is none, which no tenant identifier can match.
        """
        from docdoc.api.app import _next_cursor, _no_runs, _page_request, _summary_fields

        deployment = request.app.state.deployment
        if not deployment.has_runs:
            return _no_runs()
        bound = tenant or "*"
        refusal, before = _page_request(status, limit, cursor, cursor_tenant=bound)
        if refusal is not None:
            return refusal
        runs = deployment.runs().page_all_runs(
            tenant_id=tenant, status=status, before=before, limit=limit
        )
        return ConsoleRunListResponse(
            runs=tuple(
                ConsoleRunSummary(**_summary_fields(run), tenant_id=run.tenant_id) for run in runs
            ),
            next_cursor=_next_cursor(runs, limit, cursor_tenant=bound),
        )

    @router.get("/runs/{run_id}")
    def run_state(request: Request, run_id: str, operator: Operator) -> Any:
        """`GET /v1/runs/{run_id}`'s body, plus the run's `tenant_id`."""
        from docdoc.api.app import _routing_for

        refusal, owner, run = _run(request, run_id)
        if refusal is not None:
            return refusal
        body = {
            **RunStateResponse(**run.dump_public()).model_dump(mode="json"),
            "tenant_id": owner,
        }
        decision = _routing_for(request, run)
        if decision is not None:
            body["routing"] = decision.model_dump(mode="json")
        return JSONResponse(content=body)

    @router.get("/runs/{run_id}/delivery")
    def run_delivery(request: Request, run_id: str, operator: Operator) -> Any:
        """`GET /v1/runs/{run_id}/delivery`, looked up under the run's owner."""
        from docdoc.api.app import _iso, _unknown_run

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
        from docdoc.api.app import _ocr_document, _stored_result, _terminal
        from docdoc.runs.model import RunKind, RunStatus

        refusal, owner, run = _run(request, run_id)
        if refusal is not None:
            return refusal
        if run.kind is RunKind.OCR:
            return _ocr_document(request, owner, run)
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

    return router
