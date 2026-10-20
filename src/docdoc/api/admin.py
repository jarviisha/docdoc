"""`/v1/admin/*`: credentials and erasure, behind the `admin` scope."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from docdoc.api.auth import ADMIN_SCOPE, TENANT_PATTERN
from docdoc.api.deps import (
    Administrator,
    _deployment_of,
    _iso,
    _no_store_configured,
    _read_json,
    _unknown_run,
)
from docdoc.artifacts.paths import root_tenant


def routes() -> APIRouter:
    """The administrative routes (Milestone 10)."""
    router = APIRouter()

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
