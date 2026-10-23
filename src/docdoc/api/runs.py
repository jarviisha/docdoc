"""`/v1/documents/{blob_id}/runs`, `/v1/runs*`, corrections, and `/v1/callbacks*`."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from docdoc.api.deps import (
    _CEILINGS,
    Caller,
    _deployment_of,
    _iso,
    _no_store_configured,
    _read_json,
    _stores_of,
    _unknown_run,
)
from docdoc.api.documents import _terminal
from docdoc.api.models import RunAcceptedResponse, RunListResponse, RunStateResponse, RunSummary
from docdoc.api.paging import DEFAULT_RUN_PAGE, MAX_RUN_PAGE
from docdoc.api.settings import PRIORITY_CEILING_ENV, RUN_DATABASE_URL_ENV
from docdoc.runs.queue import Submission as _RunSpec

if TYPE_CHECKING:
    from datetime import datetime

    from docdoc.api.app import _Deployment


def routes() -> APIRouter:
    """Run submission, the run reads, corrections, and callbacks."""
    router = APIRouter()

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
            return _limit_refusal(verdict)

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

    @router.get("/runs", response_model=RunListResponse)
    async def list_runs(
        request: Request,
        principal: Caller,
        status: str | None = None,
        limit: int = DEFAULT_RUN_PAGE,
        cursor: str | None = None,
    ) -> Any:
        """This principal's runs, newest first (Milestone 11 FR-013).

        **The one new read this milestone adds**, and it is a read. Everything
        the operations console writes reaches a route Milestone 10 already
        ships; this exists because `GET /v1/runs/{run_id}` answers only for an
        identifier the caller already holds, and a surface that cannot enumerate
        cannot open.

        **No administrative scope.** A principal lists their own tenant's runs.
        Listing across tenants does not exist here, deliberately: it would be a
        second new read, and "exactly one new server-side concept" is the claim
        this milestone is measured on.

        **Removed runs are absent** (FR-020). A tombstone stays reachable by
        identity, by its owner, through the route `specs/010` FR-011 defines —
        this listing is not a second way to learn that something was deleted.
        """
        deployment = _deployment_of(request)
        if not deployment.has_runs:
            # The same answer `GET /v1/runs/{run_id}` gives a deployment with no
            # database, in the same shape: this is a configuration a deployment
            # may have, not a fault, and an empty list would read as "nothing has
            # ever been submitted" (Edge Cases).
            return _no_runs()

        refusal, before = _page_request(status, limit, cursor, cursor_tenant=principal.tenant_id)
        if refusal is not None:
            return refusal

        runs = deployment.runs().page_runs(
            principal.tenant_id,
            status=status,
            before=before,
            limit=limit,
        )

        return RunListResponse(
            runs=tuple(RunSummary(**_summary_fields(run)) for run in runs),
            next_cursor=_next_cursor(runs, limit, cursor_tenant=principal.tenant_id),
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
            return _run_erased(stone)

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
        return await record_check(request, run_id, principal.tenant_id)

    @router.get("/runs/{run_id}/corrections")
    async def list_corrections(request: Request, run_id: str, principal: Caller) -> Any:
        """This tenant's corrections against this run, and nobody else's (FR-079)."""
        return list_checks(request, run_id, principal.tenant_id)

    # -- callbacks (ADR-0018) -----------------------------------------------

    @router.post("/callbacks", status_code=201)
    async def register_callback(request: Request, principal: Caller) -> Any:
        """Register a destination. **The secret is never returned** (FR-066).

        The destination is validated here *and* again before every attempt. Only
        the second closes DNS rebinding; this one exists so an operator finds out
        about a refused destination while they are looking at the response rather
        than in a delivery that quietly never happens.

        **`_register_callback` blocks**: `deliverer.register` resolves the
        destination's address and inserts a row. Off the event loop, as the
        synchronous OCR submission route already runs its own blocking work
        (`fastapi.concurrency.run_in_threadpool`), so a slow DNS lookup here
        stalls only this request's thread, not every request this process
        serves.
        """
        from fastapi.concurrency import run_in_threadpool

        from docdoc.runs.identity import new_credential_id, now

        deployment = _deployment_of(request)
        deliverer = deployment.deliverer()
        if deliverer is None:
            return _delivery_unconfigured()

        body = await _read_json(request)
        url = str(body.get("url", "")).strip()
        secret = str(body.get("secret", "")).strip()
        return await run_in_threadpool(
            _register_callback,
            deliverer,
            tenant_id=principal.tenant_id,
            url=url,
            secret=secret,
            at=now(),
            # A callback identity is an identity like any other. `identity` is
            # the only module permitted to allocate one, and reusing the
            # credential allocator rather than adding a seventh function that
            # calls `uuid4` is the whole of what that rule buys.
            callback_id=new_credential_id(),
        )

    @router.delete("/callbacks/{callback_id}", status_code=204, response_class=Response)
    async def revoke_callback(request: Request, callback_id: str, principal: Caller) -> Response:
        """`204`, idempotent — the same shape credential revocation has.

        Malformed and unknown are one answer here for the reason they are one on
        every other route: a different one names which identifiers are
        well-formed enough to exist. `_revoke_callback` tells the two apart only
        for a caller — the console — that logs the difference; this route does
        not need to.
        """
        from docdoc.runs.identity import now

        deployment = _deployment_of(request)
        deliverer = deployment.deliverer()
        if deliverer is not None:
            _revoke_callback(deliverer, callback_id, tenant_id=principal.tenant_id, at=now())
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

    return router


#: Returned by `_requested_callback` for a callback that is unknown, malformed,
#: or another tenant's. A sentinel rather than `None`, because `None` is the
#: ordinary answer — most submissions ask to be notified about nothing.
_UNKNOWN_CALLBACK = object()


async def record_check(
    request: Request, run_id: str, tenant_id: str, annotator: str | None = None
) -> Any:
    """Record one check on a run: the Bearer route's body, and the console's.

    `annotator`, when given, overrides the body's (the console records the
    signed-in operator).
    """
    from uuid import UUID

    from docdoc.evaluation.corrections import Correction
    from docdoc.runs.corrections import ANSWERS_NEVER_EXPIRE
    from docdoc.runs.identity import now
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
    run = deployment.runs().get(identity, tenant_id)
    if run is None:
        stone = deployment.runs().tombstone(identity, tenant_id)
        if stone is None:
            return _unknown_run(run_id)
        # `410` and not `404`, and naming which: an annotation against
        # something that is gone is uninterpretable, and a reviewer who typed
        # the right identifier should not be told they typed a wrong one
        # (FR-082).
        return _run_erased(stone)

    if run.schema_identity is None:
        return JSONResponse(
            status_code=409,
            content={
                "error": "run_has_no_fields",
                "detail": "an OCR run extracts no fields, so there is nothing to check",
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
    if annotator is not None and isinstance(body, dict):
        # Set before validating: the body of a console check carries none.
        body = {**body, "annotator": annotator}
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

    if correction.corrected_value is not None:
        # Typed as the schema declares, as the dataset loader types a label:
        # `comparators@1` gates on type, so "3" never equals an extracted 3.
        from docdoc.evaluation.values import strip_indices
        from docdoc.extraction.errors import SchemaError
        from docdoc.runs.answers import field_types, typed

        try:
            declared = field_types(deployment.registry().describe(run.schema_identity)).get(
                strip_indices(correction.field_path)
            )
        except SchemaError:
            declared = None  # The version is gone; scoring types it, or fails, later.
        try:
            value = typed(correction.corrected_value, declared)
        except ValueError:
            # The path only: the value is what a reviewer typed from the document.
            return JSONResponse(
                status_code=422,
                content={"error": "invalid_value", "detail": correction.field_path},
            )
        correction = correction.model_copy(update={"corrected_value": value})

    if correction.confirmed and correction.location is None:
        # A tick carries no location of its own: it is where the run read
        # the value from, which is what the run's grounding says.
        store_for_tenant, _ = deployment.stores_for(tenant_id)
        correction = correction.model_copy(
            update={
                "location": _grounded_location(
                    store_for_tenant, run.processing_id, correction.field_path
                )
            }
        )

    at = now()
    # **A check is an answer and answers never expire.** The run it was made
    # on is pinned by it, so the run outlives neither.
    expires_at = ANSWERS_NEVER_EXPIRE

    correction_id = store.record(
        correction,
        tenant_id=tenant_id,
        run_id=identity,
        blob_id=run.blob_id,
        schema_name=run.schema_identity.split("@")[0],
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


def list_checks(request: Request, run_id: str, tenant_id: str) -> Any:
    """The tenant's checks against one run."""
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
            for correction in store.for_run(tenant_id=tenant_id, run_id=identity)
        ]
    }


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

    grounding = None if store is None else _grounding_of(store, validation)

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


def _grounded_location(store: Any, processing_id: str | None, field_path: str) -> Any:
    """Where the run read this field from, or ``None`` when it is ungrounded."""
    from docdoc.evaluation.labels import ExpectedLocation

    validation = _terminal(store, processing_id) if processing_id else None
    if validation is None:
        return None
    outcome = _grounding_of(store, validation).outcomes.get(field_path)
    if outcome is None or not outcome.geometry:
        return None
    geometry = outcome.geometry[0]
    return ExpectedLocation(page=geometry.page_index, bbox=geometry.bbox)


def _grounding_of(store: Any, validation: Any) -> Any:
    """The grounding artifact a validation result was derived from."""
    from docdoc.grounding.result import GroundingResult
    from docdoc.pipeline.stages import Stage, spec_for

    return store.get(
        validation.provenance.grounding_artifact_id,
        model=GroundingResult,
        artifact_format_version=spec_for(Stage.GROUND).artifact_format_version,
    )


def _limit_refusal(verdict: Any) -> JSONResponse:
    """The 429 for a tenant over its limits; shared by every route that queues runs."""
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


def _run_erased(stone: Any) -> JSONResponse:
    """The 410 a run's owner gets for a removed run (`specs/010` FR-011).

    When, and under which policy, and nothing else: a tombstone holds four
    fields precisely so that being told about one discloses nothing about what
    it held.
    """
    return JSONResponse(
        status_code=410,
        content={
            "error": "run_erased",
            "run_id": str(stone.run_id),
            "deleted_at": stone.deleted_at.isoformat(),
            "policy": stone.policy,
        },
    )


def _no_runs() -> JSONResponse:
    """`503` for a run read on a deployment that records no runs.

    A configuration a deployment may have, not a fault; an empty list would read
    as "nothing has ever been submitted".
    """
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "class": "RunStateUnavailableError",
                "message": "this deployment records no runs",
            }
        },
    )


def _page_request(
    status: str | None, limit: int, cursor: str | None, *, cursor_tenant: str
) -> tuple[JSONResponse | None, tuple[Any, Any] | None]:
    """`(refusal, before)` for a run listing's query: one of the two is `None`.

    Shared by `GET /v1/runs` and the console's listing, so the two validate a
    page identically. `cursor_tenant` is what a cursor is bound to.
    """
    from docdoc.api.paging import CursorError, decode
    from docdoc.runs.model import RunStatus

    def refuse(code: int, error_class: str, message: str) -> tuple[JSONResponse, None]:
        return JSONResponse(
            status_code=code, content={"error": {"class": error_class, "message": message}}
        ), None

    if status is not None and status not in {member.value for member in RunStatus}:
        # A `422` and not an empty page. The set is the five members
        # `specs/009` closed it at, and a filter nobody can satisfy is a
        # client bug the deployment should name rather than absorb.
        return refuse(
            422,
            "InvalidRunStatus",
            "status must be one of " + ", ".join(sorted(member.value for member in RunStatus)),
        )

    if limit < 1 or limit > MAX_RUN_PAGE:
        return refuse(422, "InvalidPageSize", f"limit must be between 1 and {MAX_RUN_PAGE}")

    if cursor is None:
        return None, None
    try:
        position = decode(cursor, tenant_id=cursor_tenant)
    except CursorError:
        # One answer for malformed and for another tenant's, because two
        # would tell a caller which foreign cursors are well formed (FR-016).
        return refuse(400, "InvalidCursor", "this cursor cannot be read")
    return None, (position.created_at, position.run_id)


def _finished_at(status: str, updated_at: datetime) -> str | None:
    """When a run finished: its `updated_at` once terminal, `None` before.

    `updated_at` is when a terminal run reached its terminal state. `None`
    while it has not: an absent field says "still going", and a timestamp that
    moved with every heartbeat would not.
    """
    from docdoc.runs.model import TERMINAL_STATES

    return updated_at.isoformat() if status in TERMINAL_STATES else None


def _summary_fields(run: Any) -> dict[str, Any]:
    """A run as a listing row: `RunSummary`'s fields, shared with the console's."""
    return {
        "run_id": str(run.run_id),
        "status": str(run.status),
        "created_at": run.created_at.isoformat(),
        "finished_at": _finished_at(run.status, run.updated_at),
        "blob_id": run.blob_id,
        "schema_identity": run.schema_identity,
        "kind": str(run.kind),
        "processing_id": run.processing_id,
    }


def _next_cursor(runs: Any, limit: int, *, cursor_tenant: str) -> str | None:
    """The cursor after a page, bound to `cursor_tenant`; `None` on the last page.

    A full page means there may be more; a short one is the end. One extra
    round trip on the boundary case, against a `COUNT(*)` on every page.
    """
    from docdoc.api.paging import Cursor, encode

    if len(runs) != limit:
        return None
    last = runs[-1]
    return encode(Cursor(tenant_id=cursor_tenant, created_at=last.created_at, run_id=last.run_id))


def _register_callback(
    deliverer: Any, *, tenant_id: str, url: str, secret: str, at: datetime, callback_id: Any
) -> JSONResponse:
    """Validate, register, and map the result — the one body `POST /v1/callbacks`
    and the console's own registration both answer with.

    Shared so the two 422s (a missing field, a refused destination) are written
    once: a second copy drifting from this one is exactly how a console route
    would end up reporting a refusal differently from the tenant route it
    mirrors.

    **Raises, rather than returns, for the missing-field case.** `/v1/*` routes
    are unchanged by this task (a global constraint — `POST /v1/callbacks` has
    an API-key caller, mmerp, that already parses its answer), so this keeps
    the exact body that route always gave: FastAPI's default `HTTPException`
    handler wraps `detail` as `{"detail": {...}}`, and a caller of this helper
    that caught the exception and returned a flat body instead would be a
    second, different shape for the same refusal.
    """
    from docdoc.runs.delivery import DeliveryError

    if not url or not secret:
        raise HTTPException(
            status_code=422,
            detail={"error": "invalid_callback", "detail": "url and secret are required"},
        )
    try:
        callback = deliverer.register(
            tenant_id=tenant_id, url=url, secret=secret, at=at, callback_id=callback_id
        )
    except DeliveryError as error:
        # The class of refusal and **not the addresses it resolved to**. A 422
        # that reported them would make this route a name resolver for an
        # authenticated caller, which is a capability nobody asked for.
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


def _revoke_callback(deliverer: Any, callback_id: str, *, tenant_id: str, at: datetime) -> bool:
    """Revoke, and say whether anything was. `False` for a malformed identifier
    too — there is nothing by that name to revoke either.

    `DELETE /v1/callbacks/{id}` answers `204` either way and does not need the
    distinction; the console logs `console.callback_revoke` only when it is
    `True`, which is why this is a function and not inlined into that route.
    """
    from uuid import UUID

    try:
        identity = UUID(callback_id)
    except ValueError:
        return False
    return bool(deliverer.revoke(identity, tenant_id=tenant_id, at=at))


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
