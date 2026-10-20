"""What the `/v1` route modules share: the caller, the deployment, small answers.

Imports no route module, so every route module can import it.
"""

from __future__ import annotations

import json
import os
from typing import TYPE_CHECKING, Annotated, Any

from fastapi import Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from docdoc.api.auth import ADMIN_SCOPE, Principal, bearer_of
from docdoc.api.settings import LIMITS_FILE_ENV, STORE_ROOT_ENV

if TYPE_CHECKING:
    from docdoc.api.app import _Deployment


def _deployment_of(request: Request) -> _Deployment:
    return request.app.state.deployment  # type: ignore[no-any-return]


async def principal_of(request: Request) -> Principal:
    """Resolve the caller to exactly one tenant, or refuse (FR-059, FR-060).

    A router-level dependency, so FastAPI resolves it **before** the endpoint
    body runs — which is what makes FR-067 true rather than aspirational. Every
    route on the `/v1` router reads its request body inside the handler
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


def _unknown_run(run_id: str) -> JSONResponse:
    """The one answer for unknown, malformed, and another tenant's (FR-066).

    One function so the three cannot drift apart into three messages, which is
    how an existence oracle gets written by accident.
    """
    return JSONResponse(
        status_code=404,
        content={"error": {"class": "RunNotFoundError", "message": "no such run here"}},
    )


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


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


#: Per-tenant ceilings, read once at import for the reason the key file and the
#: limits file are read once: a deployment changing a customer's ceiling is
#: making a configuration change, and it restarts for every other one.
_CEILINGS: dict[str, int] = {}


#: Per-tenant OCR caps (`ocr_queue` in the limits file, ADR-0022), read once
#: beside the ceilings and for the same reasons.
_OCR_QUEUES: dict[str, int] = {}
#: `default.ocr_queue` from the same file, when it names one. Overrides the
#: environment's per-tenant default for every tenant without its own entry, as
#: the file's `default` does for the four limits.
_OCR_QUEUE_DEFAULT: list[int] = []


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
    _OCR_QUEUES.clear()
    _OCR_QUEUE_DEFAULT.clear()
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
        cap = _ocr_queue_of(entry, where=tenant_id)
        if cap is not None:
            _OCR_QUEUES[tenant_id] = cap
    default_cap = _ocr_queue_of(raw.get("default"), where="default")
    if default_cap is not None:
        _OCR_QUEUE_DEFAULT.append(default_cap)


def _ocr_queue_of(entry: Any, *, where: str) -> int | None:
    """An entry's `ocr_queue`, or `None` when it names none. Refused when malformed.

    Strict where the ceiling is lenient, and on purpose: a ceiling that falls
    back grants *less*, while an OCR cap that is silently dropped admits up to
    the default when the operator meant fewer -- the "believes it is protected
    and is not" failure `LimitBook` refuses for the four limits (ADR-0022).
    """
    if not isinstance(entry, dict) or "ocr_queue" not in entry:
        return None
    cap = entry["ocr_queue"]
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
        raise ValueError(
            f"{LIMITS_FILE_ENV}: `ocr_queue` for {where!r} must be a positive integer, not {cap!r}"
        )
    return cap


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
