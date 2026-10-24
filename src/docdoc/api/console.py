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
import os
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from docdoc.api.admin import _audit, _erase
from docdoc.api.deps import _iso, _read_capped, _read_json, _unknown_run
from docdoc.api.documents import _stored_result, _terminal
from docdoc.api.evaluations import (
    answers_of,
    list_evaluations_of,
    read_evaluation_of,
    start_evaluation_of,
)
from docdoc.api.models import (
    ConsoleRunListResponse,
    ConsoleRunSummary,
    ParserListing,
    RunStateResponse,
)
from docdoc.api.ocr import _ocr_document, ocr_unavailable, parser_listing, submit_ocr
from docdoc.api.paging import DEFAULT_RUN_PAGE
from docdoc.api.runs import (
    _delivery_unconfigured,
    _finished_at,
    _next_cursor,
    _no_runs,
    _page_request,
    _register_callback,
    _revoke_callback,
    _routing_for,
    _run_erased,
    _summary_fields,
    list_checks,
    record_check,
    submit_extraction,
)
from docdoc.artifacts.paths import root_tenant
from docdoc.extraction.errors import SchemaError
from docdoc.runs import identity
from docdoc.runs.console_schemas import SchemaNameTaken, SchemaVersionTaken, create_version
from docdoc.runs.model import Priority
from docdoc.runs.principal import TENANT_PATTERN
from docdoc.runs.provider_settings import DEFAULT_SCOPE, KEEP_KEY
from docdoc.runs.providers import KNOWN_ADAPTERS, registry_for, resolve, selected_id
from docdoc.runs.secrets import last4

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
        return _refusal(503, "ConsoleUnavailable", _CONSOLE_UNAVAILABLE)
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


def _log_extract_submit(
    operator: str, *, tenant_id: str, run_id: str, schema: str, status: int
) -> None:
    """One structured event per accepted console extraction, as `_log_ocr_submit` does."""
    logging.getLogger("docdoc.api").info(
        json.dumps(
            {
                "event": "console.extract_submit",
                "actor": f"console:{operator}",
                "tenant_id": tenant_id,
                "run_id": run_id,
                "schema": schema,
                "status": status,
            }
        )
    )


def _log_evaluation_start(operator: str, *, tenant_id: str, schema: str, status: int) -> None:
    """One structured event per console evaluation start, attributed, with its outcome.

    Starting one re-runs a tenant's checked documents, which may call paid
    providers on the tenant's behalf; a refused start is logged too, as a
    refused erasure is.
    """
    logging.getLogger("docdoc.api").info(
        json.dumps(
            {
                "event": "console.evaluation_start",
                "actor": f"console:{operator}",
                "tenant_id": tenant_id,
                "schema": schema,
                "status": status,
            }
        )
    )


def _log_callback_register(
    operator: str, *, tenant_id: str, callback_id: str, url: str, status: int
) -> None:
    """One structured event per *successful* console registration, as
    `_log_ocr_submit` is — there is nothing to attribute for a refusal, since
    nothing was stored. The URL is logged and the secret is never a parameter
    this function could log even by mistake.
    """
    logging.getLogger("docdoc.api").info(
        json.dumps(
            {
                "event": "console.callback_register",
                "actor": f"console:{operator}",
                "tenant_id": tenant_id,
                "callback_id": callback_id,
                "url": url,
                "status": status,
            }
        )
    )


def _log_callback_revoke(operator: str, *, tenant_id: str, callback_id: str, status: int) -> None:
    """One structured event per console revoke that actually revoked something."""
    logging.getLogger("docdoc.api").info(
        json.dumps(
            {
                "event": "console.callback_revoke",
                "actor": f"console:{operator}",
                "tenant_id": tenant_id,
                "callback_id": callback_id,
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


def _for_tenant(request: Request, tenant_id: str, call: Any) -> Any:
    """A tenant-path read: the tenant id is validated, and no database is a 503."""
    if not TENANT_PATTERN.match(tenant_id):
        return JSONResponse(status_code=422, content={"error": "invalid_tenant_id"})
    if not request.app.state.deployment.has_runs:
        return _no_runs()
    return call()


class _NewSchema(BaseModel):
    document: dict[str, Any]
    prompt: str
    base: str | None = None


_CONSOLE_UNAVAILABLE = (
    "the console needs a run-state database; set DOCDOC_RUN_DATABASE_URL and run docdoc migrate"
)


def _schema_detail(registry: Any, store: Any, identity: str) -> dict[str, Any]:
    """A schema's detail: a console row's stored JSON, else the file schema's dump."""
    entry = registry.resolve(identity)
    row = store.get(identity) if store is not None and entry.source == "console" else None
    return {
        "identity": identity,
        "source": entry.source,
        "document": row.document
        if row
        else entry.schema.model_dump(mode="json", exclude_defaults=True),
        "prompt": entry.prompt.text,
        "base": row.base if row else None,
        "created_at": _iso(row.created_at) if row else None,
        "created_by": row.created_by if row else None,
    }


#: Adapters that answer from fixtures: shown, ordered, never configured.
_TEST_ONLY = frozenset({"echo"})
_REDACTED = "••••"
#: A provider body is a model name and a key; anything near this is not one.
_PROVIDER_BODY_CAP = 16 * 1024
_MODEL_MAX = 256


async def _provider_json(request: Request) -> dict[str, Any]:
    """A provider body as a mapping, read under `_PROVIDER_BODY_CAP`; nothing echoed."""
    raw = await _read_capped(request, _PROVIDER_BODY_CAP)
    try:
        parsed = json.loads(raw) if raw else {}
    except ValueError:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_json", "message": _BAD_MESSAGES["invalid_json"]},
        ) from None
    return parsed if isinstance(parsed, dict) else {}


_BAD_MESSAGES = {
    "invalid_json": "the request body is not valid JSON",
    "invalid_tenant_id": "the tenant id is not valid",
    "tenant_required": "choose a tenant; the deployment default cannot be reset",
    "invalid_priority": "priority must be a non-empty list of distinct known adapters",
    "invalid_model": "model must be non-blank text of at most 256 characters",
    "invalid_api_key": "api_key must be non-blank text",
}


def _bad(error: str) -> JSONResponse:
    """A 422 naming the field only: a provider body may carry a key, never echoed."""
    message = _BAD_MESSAGES.get(error, "the request was not valid")
    return JSONResponse(status_code=422, content={"error": error, "message": message})


def _provider_scope(request: Request, tenant: str | None) -> tuple[JSONResponse | None, Any]:
    """The provider store, or the refusal: a malformed tenant, or no database."""
    if tenant is not None and not TENANT_PATTERN.match(tenant):
        return _bad("invalid_tenant_id"), None
    store = request.app.state.deployment.provider_store()
    if store is None:
        return _refusal(503, "ConsoleUnavailable", _CONSOLE_UNAVAILABLE), None
    return None, store


def _optional_text(body: dict[str, Any], name: str) -> str | None:
    """A stripped string or ``None``; `ValueError` for blank text or another type."""
    value = body.get(name)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(name)
    return value.strip()


def _providers_body(request: Request, tenant: str | None) -> dict[str, Any]:
    """What `GET /v1/console/providers` answers for a scope. Never a key."""
    deployment = request.app.state.deployment
    resolved = resolve(tenant, deployment.provider_store(), deployment.sealer())
    status = {c.id: c for c in registry_for(resolved).candidates()}
    return {
        # `None` is the deployment default: "default" is a legal tenant id.
        "scope": tenant,
        "secrets_configured": deployment.sealer() is not None,
        "priority": {"value": list(resolved.priority), "source": resolved.priority_source},
        "selected": selected_id(resolved),
        "adapters": [
            {
                "id": a.id,
                "test_only": a.id in _TEST_ONLY,
                "model": {"value": a.model, "source": a.model_source},
                "key": {"last4": a.key_last4, "source": a.key_source},
                "status": {"usable": status[a.id].available, "reason": status[a.id].reason},
                "updated_at": _iso(a.updated_at),
                "updated_by": a.updated_by,
            }
            for a in resolved.adapters
        ],
    }


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
        """Page `page` (0-based) of a run's source document, as an image (019).

        PNG and JPEG are served as stored, page 0 only; a PDF page is rendered
        to PNG, longer edge at most 2000 px. An out-of-range page and a missing
        blob are the one 404 an unknown run gets. Plain `def`: rendering blocks.
        """
        from docdoc.api.pages import page_image
        from docdoc.runs.model import RunKind, RunStatus

        refusal, owner, run = _run(request, run_id)
        if refusal is not None:
            return refusal
        # A succeeded extraction run's pages too: its fields are checked beside
        # the page they were read from (ADR-0026 amends ADR-0023 §4).
        if run.kind is RunKind.EXTRACTION and run.status is not RunStatus.SUCCEEDED:
            return JSONResponse(
                status_code=409,
                content={"error": "run_not_succeeded", "status": str(run.status)},
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

    @router.get("/schemas")
    def list_schemas(request: Request, operator: Operator) -> Any:
        """Every identity with its source; console rows add who made them and when."""
        deployment = request.app.state.deployment
        registry = deployment.registry()
        store = deployment.console_schemas()
        rows = {r.identity: r for r in (store.all() if store is not None else ())}
        entries = []
        for e in registry.entries():
            row = rows.get(e.identity) if e.source == "console" else None
            entries.append(
                {
                    "identity": e.identity,
                    "name": e.schema.name,
                    "version": e.schema.version,
                    "source": e.source,
                    "field_count": len(e.schema.fields),
                    "created_at": _iso(row.created_at) if row else None,
                    "created_by": row.created_by if row else None,
                    "shadows_file": row is not None and e.identity in registry.shadowed,
                }
            )
        return {"entries": entries}

    @router.get("/schemas/{identity}")
    def read_schema(request: Request, operator: Operator, identity: str) -> Any:
        """One schema's document and prompt; 404 when the identity is unknown."""
        deployment = request.app.state.deployment
        registry = deployment.registry()
        if identity not in registry:
            return JSONResponse(status_code=404, content={"error": "unknown_schema"})
        return _schema_detail(registry, deployment.console_schemas(), identity)

    @router.post("/schemas", status_code=201)
    def create_schema(request: Request, operator: Operator, body: _NewSchema) -> Any:
        """Save a new version: the server assigns the number (409 if it cannot)."""
        deployment = request.app.state.deployment
        store = deployment.console_schemas()
        if store is None:
            return _refusal(503, "ConsoleUnavailable", _CONSOLE_UNAVAILABLE)
        registry = deployment.registry()
        try:
            row = create_version(
                store,
                registry,
                document=body.document,
                prompt=body.prompt,
                base=body.base,
                created_by=operator,
                now=datetime.now(UTC),
            )
        except (SchemaNameTaken, SchemaVersionTaken) as exc:
            code = (
                "schema_name_taken" if isinstance(exc, SchemaNameTaken) else "schema_version_taken"
            )
            return JSONResponse(status_code=409, content={"error": code, "message": str(exc)})
        except SchemaError as exc:
            problems = [{"path": p, "message": m} for p, m in exc.problems]
            if not problems and exc.field_path:
                problems = [{"path": exc.field_path, "message": str(exc)}]
            return JSONResponse(
                status_code=422,
                content={"error": "invalid_schema", "message": str(exc), "problems": problems},
            )
        return JSONResponse(
            status_code=201, content=_schema_detail(deployment.registry(), store, row.identity)
        )

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

    @router.post("/tenants/{tenant_id}/extractions", status_code=202)
    async def submit_tenant_extraction(
        request: Request, tenant_id: str, operator: Operator, schema: str
    ) -> Any:
        """Store the body as a document and submit an extraction run, for a named tenant.

        `POST /v1/documents` and `POST /v1/documents/{blob_id}/runs` in one
        session-only call, through the same `submit_extraction`, so the caps, the
        limiter and idempotency are the tenant's own.
        """
        if not TENANT_PATTERN.match(tenant_id):
            return JSONResponse(status_code=422, content={"error": "invalid_tenant_id"})
        deployment = request.app.state.deployment
        if not deployment.has_runs:
            return _no_runs()
        data = await _read_capped(request, deployment.max_request_bytes)
        response = await submit_extraction(
            request,
            tenant_id=tenant_id,
            data=data,
            schema=schema,
            # An operator submitting here is waiting at the screen (019).
            priority=Priority.URGENT,
        )
        if response.status_code not in (200, 202):
            return response
        # The accepted body (`run_id`, `status`, `created_at`, `priority`) plus the
        # tenant. `_owned` wants a full run state, which a submission does not
        # return; the run's detail is one `GET /v1/console/runs/{run_id}` away.
        body = {**json.loads(bytes(response.body)), "tenant_id": tenant_id}
        _log_extract_submit(
            operator,
            tenant_id=tenant_id,
            run_id=body["run_id"],
            schema=schema,
            status=response.status_code,
        )
        return JSONResponse(status_code=response.status_code, content=body)

    # -- checks and evaluations (ADR-0023 §3) --------------------------------
    #
    # The tenant's own bodies, on a named tenant's behalf. A check made here is
    # the operator's: `annotator` is the signed-in username, whatever the body says.

    @router.post("/runs/{run_id}/corrections", status_code=201)
    async def record_run_check(request: Request, run_id: str, operator: Operator) -> Any:
        refusal, owner, _ = _owner(request, run_id)
        if refusal is not None:
            return refusal
        return await record_check(request, run_id, owner, annotator=operator)

    @router.get("/runs/{run_id}/corrections")
    def list_run_checks(request: Request, run_id: str, operator: Operator) -> Any:
        refusal, owner, _ = _owner(request, run_id)
        if refusal is not None:
            return refusal
        return list_checks(request, run_id, owner)

    @router.get("/tenants/{tenant_id}/answers")
    def tenant_answers(request: Request, tenant_id: str, operator: Operator) -> Any:
        return _for_tenant(request, tenant_id, lambda: answers_of(request, tenant_id))

    @router.post("/tenants/{tenant_id}/evaluations", status_code=202)
    def start_tenant_evaluation(
        request: Request, tenant_id: str, schema: str, operator: Operator
    ) -> Any:
        response = _for_tenant(
            request, tenant_id, lambda: start_evaluation_of(request, tenant_id, schema)
        )
        _log_evaluation_start(
            operator,
            tenant_id=tenant_id,
            schema=schema,
            status=getattr(response, "status_code", 200),
        )
        return response

    @router.get("/tenants/{tenant_id}/evaluations")
    def list_tenant_evaluations(
        request: Request,
        tenant_id: str,
        operator: Operator,
        schema: str | None = None,
        limit: int = 20,
    ) -> Any:
        return _for_tenant(
            request, tenant_id, lambda: list_evaluations_of(request, tenant_id, schema, limit)
        )

    @router.get("/tenants/{tenant_id}/evaluations/{evaluation_id}")
    def read_tenant_evaluation(
        request: Request, tenant_id: str, evaluation_id: str, operator: Operator
    ) -> Any:
        return _for_tenant(
            request, tenant_id, lambda: read_evaluation_of(request, tenant_id, evaluation_id)
        )

    # -- callbacks, per tenant (ADR-0018, ADR-0023 §3) -----------------------
    #
    # Three routes over `PostgresDeliverer.register`/`revoke`/`callbacks_for`,
    # on a named tenant's behalf. The 422s and the 503 are `_register_callback`'s
    # and `_delivery_unconfigured`'s — the same bodies `POST /v1/callbacks`
    # answers with, not a second copy of either (FR-066: the secret is never
    # returned or logged, on this route or that one).

    @router.get("/tenants/{tenant_id}/callbacks")
    def list_tenant_callbacks(request: Request, tenant_id: str, operator: Operator) -> Any:
        """A tenant's active callbacks, newest first. Never the secret or its digest.

        There is no listing route under `/v1/callbacks` itself (FR-066's other
        half) -- this is the console enumerating a tenant's destinations on
        their behalf, behind a session rather than that tenant's own key.
        """
        if not TENANT_PATTERN.match(tenant_id):
            return JSONResponse(status_code=422, content={"error": "invalid_tenant_id"})
        deliverer = request.app.state.deployment.deliverer()
        if deliverer is None:
            return _delivery_unconfigured()
        return {
            "callbacks": [
                {
                    "callback_id": str(callback.callback_id),
                    "tenant_id": callback.tenant_id,
                    "url": callback.url,
                    "created_at": _iso(callback.created_at),
                }
                for callback in deliverer.callbacks_for(tenant_id)
            ]
        }

    @router.post("/tenants/{tenant_id}/callbacks", status_code=201)
    async def register_tenant_callback(request: Request, tenant_id: str, operator: Operator) -> Any:
        """`POST /v1/callbacks` for a named tenant, through the same `register`.

        Logged only on success, as `submit_tenant_ocr` logs `console.ocr_submit`
        only on success -- a refusal stored nothing, so there is no `callback_id`
        to attribute it to. The body is read as `POST /v1/callbacks` reads it, so
        a malformed field gets the same `invalid_callback` sentence rather than a
        validation error echoing the input back -- the secret included.
        """
        from fastapi.concurrency import run_in_threadpool

        if not TENANT_PATTERN.match(tenant_id):
            return JSONResponse(status_code=422, content={"error": "invalid_tenant_id"})
        deliverer = request.app.state.deployment.deliverer()
        if deliverer is None:
            return _delivery_unconfigured()

        body = await _read_json(request)
        url = str(body.get("url", "")).strip()
        response = await run_in_threadpool(
            _register_callback,
            deliverer,
            tenant_id=tenant_id,
            url=url,
            secret=str(body.get("secret", "")).strip(),
            at=identity.now(),
            callback_id=identity.new_credential_id(),
        )
        if response.status_code != 201:
            return response
        payload = {**json.loads(bytes(response.body)), "tenant_id": tenant_id}
        _log_callback_register(
            operator, tenant_id=tenant_id, callback_id=payload["callback_id"], url=url, status=201
        )
        return JSONResponse(status_code=201, content=payload)

    @router.delete("/tenants/{tenant_id}/callbacks/{callback_id}", status_code=204)
    def revoke_tenant_callback(
        request: Request, tenant_id: str, callback_id: str, operator: Operator
    ) -> Response:
        """`DELETE /v1/callbacks/{id}` for a named tenant: `204`, idempotently.

        Logged only when a callback was actually revoked -- the same "a no-op
        is not an event" `_log_ocr_submit`'s success-only logging keeps, made
        visible here because this acts on someone else's registration.
        """
        if not TENANT_PATTERN.match(tenant_id):
            return JSONResponse(status_code=422, content={"error": "invalid_tenant_id"})
        deliverer = request.app.state.deployment.deliverer()
        if deliverer is None:
            return Response(status_code=204)

        revoked = _revoke_callback(deliverer, callback_id, tenant_id=tenant_id, at=identity.now())
        if revoked:
            _log_callback_revoke(operator, tenant_id=tenant_id, callback_id=callback_id, status=204)
        return Response(status_code=204)

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

    # The provider routes: one scope per call, the deployment default without
    # `tenant`. Bodies are read raw, as the callback routes read theirs, so a
    # malformed field gets a field name back rather than a validation error
    # echoing the input -- the API key included.

    @router.get("/providers")
    def read_providers(request: Request, operator: Operator, tenant: str | None = None) -> Any:
        refused, _ = _provider_scope(request, tenant)
        return refused or _providers_body(request, tenant)

    @router.put("/providers/priority")
    async def set_provider_priority(
        request: Request, operator: Operator, tenant: str | None = None
    ) -> Any:
        """``{"priority": [ids] | null}``; null inherits, an empty list means nothing."""
        from fastapi.concurrency import run_in_threadpool

        refused, store = _provider_scope(request, tenant)
        if refused:
            return refused
        body = await _provider_json(request)
        value = body.get("priority")
        if "priority" not in body or (
            value is not None
            and (
                not isinstance(value, list)
                or not value
                or not all(isinstance(v, str) and v in KNOWN_ADAPTERS for v in value)
                or len(set(value)) != len(value)
            )
        ):
            return _bad("invalid_priority")
        await run_in_threadpool(
            store.set_priority,
            tenant or DEFAULT_SCOPE,
            None if value is None else tuple(value),
            by=operator,
            now=identity.now(),
        )
        return await run_in_threadpool(_providers_body, request, tenant)

    @router.put("/providers/{adapter_id}")
    async def set_provider(
        request: Request, adapter_id: str, operator: Operator, tenant: str | None = None
    ) -> Any:
        """``{"model": str | null, "api_key"?: str | null}``: absent keeps the key."""
        from fastapi.concurrency import run_in_threadpool

        refused, store = _provider_scope(request, tenant)
        if refused:
            return refused
        if adapter_id not in KNOWN_ADAPTERS or adapter_id in _TEST_ONLY:
            return JSONResponse(status_code=404, content={"error": "unknown_adapter"})
        body = await _provider_json(request)
        if "model" not in body:
            # Required, so a body naming only a key cannot clear the model by omission.
            return _bad("invalid_model")
        try:
            model = _optional_text(body, "model")
            api_key = _optional_text(body, "api_key")
        except ValueError as exc:
            return _bad(f"invalid_{exc}")
        if model is not None and len(model) > _MODEL_MAX:
            return _bad("invalid_model")
        key: Any = KEEP_KEY if "api_key" not in body else None
        if api_key is not None:
            sealer = request.app.state.deployment.sealer()
            if sealer is None:
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "secrets_not_configured",
                        "message": "set DOCDOC_SECRETS_KEY to store API keys",
                    },
                )
            key = (sealer.seal(api_key), last4(api_key))
        await run_in_threadpool(
            store.set_adapter,
            tenant or DEFAULT_SCOPE,
            adapter_id,
            model=model,
            key=key,
            by=operator,
            now=identity.now(),
        )
        return await run_in_threadpool(_providers_body, request, tenant)

    @router.delete("/providers")
    def reset_providers(request: Request, operator: Operator, tenant: str | None = None) -> Any:
        """Drop a tenant's overrides; the deployment default is never reset."""
        refused, store = _provider_scope(request, tenant)
        if refused:
            return refused
        if tenant is None:
            return _bad("tenant_required")
        store.reset(tenant)
        return _providers_body(request, tenant)

    @router.post("/providers/{adapter_id}/test")
    async def test_provider(
        request: Request, adapter_id: str, operator: Operator, tenant: str | None = None
    ) -> Any:
        """Probe one adapter with the scope's settings and the form's overlaid.

        Nothing is stored, so a typed key is testable without `DOCDOC_SECRETS_KEY`.
        The provider's text comes back with every occurrence of the key redacted.
        """
        from dataclasses import replace

        from fastapi.concurrency import run_in_threadpool

        refused, store = _provider_scope(request, tenant)
        if refused:
            return refused
        if adapter_id not in KNOWN_ADAPTERS:
            return JSONResponse(status_code=404, content={"error": "unknown_adapter"})
        body = await _provider_json(request)
        try:
            model = _optional_text(body, "model")
            api_key = _optional_text(body, "api_key")
        except ValueError as exc:
            return _bad(f"invalid_{exc}")
        if model is not None and len(model) > _MODEL_MAX:
            return _bad("invalid_model")

        def probe() -> dict[str, Any]:
            from docdoc.extraction.adapter_registry import default_adapter_registry

            deployment = request.app.state.deployment
            resolved = resolve(tenant, store, deployment.sealer())
            config = next(a.config for a in resolved.adapters if a.id == adapter_id)
            if model is not None:
                config = replace(config, model=model)
            if api_key is not None:
                # A typed key replaces an unreadable saved one, too.
                config = replace(config, api_key=api_key, unavailable=None)
            candidate = next(
                c
                for c in default_adapter_registry(configure={adapter_id: config}).candidates()
                if c.id == adapter_id
            )
            from docdoc.extraction.adapters.gemini import GeminiAdapter

            if isinstance(candidate.adapter, GeminiAdapter):
                result = candidate.adapter.probe()
                ok, message = result.usable, result.reason
            else:
                ok, message = candidate.available, candidate.reason
            message = message or ("connected" if ok else "unavailable")
            # The key the adapter used: typed, else saved, else the environment's.
            secret = (
                api_key
                or config.api_key
                or os.environ.get("GEMINI_API_KEY")
                or os.environ.get("GOOGLE_API_KEY")
            )
            if secret:
                message = message.replace(secret, _REDACTED)
            return {"ok": ok, "message": message}

        return await run_in_threadpool(probe)

    return router
