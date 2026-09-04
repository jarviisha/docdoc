"""``GET /healthz`` and ``GET /readyz``, and where they deliberately sit.

**Outside ``/v1``**, because they are not part of the document API a caller
writes against — they are how an orchestrator decides whether to send it traffic,
and versioning that would mean a load-balancer configuration with a version
number in it.

**Outside authentication** (FR-058). A probe has no credential and cannot be
given one: kubelet, an ELB target group, and Docker's ``HEALTHCHECK`` all issue a
bare request. Requiring a key here would make every authenticated deployment
permanently unhealthy, which is the failure mode where the security control takes
the service down.

That exemption is why the bodies say so little. Neither route discloses a
configuration value, a credential, a tenant identifier, or a count of anything
stored — a readiness body naming the database's host would be an unauthenticated
route publishing the deployment's topology.

**The decisions are not made here.** ``docdoc.runs.health`` holds them, because
the worker answers the same two questions and has no FastAPI. This module is the
HTTP shape of an answer computed one layer down (FR-053, FR-054).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi.responses import JSONResponse

from docdoc.runs.health import (
    LIVENESS_PATH,
    READINESS_PATH,
    Readiness,
    liveness_body,
    readiness_body,
)

if TYPE_CHECKING:
    from fastapi import FastAPI

__all__ = ["install"]


def install(app: FastAPI, readiness: Readiness) -> None:
    """Register both routes on the application, unversioned and unauthenticated.

    Takes the probe rather than building one: the API's dependencies are resolved
    once at startup and held on the app, and a readiness check that opened its own
    connection would be checking something other than the thing serving requests
    — passing while the real one was broken.
    """

    @app.get(LIVENESS_PATH, include_in_schema=False)
    async def healthz() -> JSONResponse:
        """A constant. Touches no database, no store, and no provider (FR-053).

        Nothing is awaited and nothing is read. A liveness probe that checked a
        dependency would restart this process because *another* one is down,
        turning a recoverable outage into a crash loop across the fleet.
        """
        return JSONResponse(status_code=200, content=liveness_body())

    @app.get(READINESS_PATH, include_in_schema=False)
    def readyz() -> JSONResponse:
        """Ready, or 503 naming the unmet dependency (FR-055).

        **Not ``async``, and the difference is the whole process.**
        ``Readiness.unmet`` is blocking I/O — a psycopg connect and an S3
        ``head_object`` — and an ``async def`` handler runs on the event loop,
        so every uncached probe froze the loop for the duration. Under an outage
        that duration is the connect timeout, and it arrives on every probe
        interval, so the readiness route took down the synchronous routes it
        exists to report on. A plain ``def`` sends it to FastAPI's threadpool,
        where blocking work belongs. ``healthz`` may stay ``async`` because it
        touches nothing.

        **Strict**: a process that cannot reach the run-state database reports
        not ready even though the synchronous routes would still serve every
        request correctly (FR-087). This withdraws working capacity on purpose.
        The alternative is a per-capability readiness signal, and no
        orchestrator's probe can express "route the synchronous half here" — so a
        richer answer would be one nothing could consume, which is worse than a
        pessimistic one that everything can.
        """
        unmet = readiness.unmet()
        return JSONResponse(
            status_code=200 if not unmet else 503,
            content=readiness_body(unmet),
            # What "retryable" means to a client that is not an orchestrator.
            # One second, because readiness is cached for two and answering
            # sooner would return the same cached answer.
            headers={} if not unmet else {"Retry-After": "1"},
        )
