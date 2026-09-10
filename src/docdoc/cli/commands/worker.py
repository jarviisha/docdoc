"""``docdoc worker`` — claim runs and execute them, one at a time.

A subcommand rather than a second console script, for the reason
``pyproject.toml`` already records about ``docdoc-api``: ``[project.scripts]``
cannot be conditioned on an extra, so a second entry point would install for
everyone including everyone without ``docdoc[postgres]``, and fail at import.

**No ``--concurrency``** (FR-025, research R9a). A worker executes one run at a
time and concurrency is replica count. The flag is absent rather than accepting
only ``1``, because a flag that takes one value is an invitation to make it take
more — and the reason it must not is a correctness one: PyMuPDF and ``rapidfuzz``
hold the GIL in bursts, so a threaded worker lets one long parse starve a
sibling's heartbeat until that sibling loses a lease it is still executing.

This command blocks until it is signalled. That is what it is for.
"""

from __future__ import annotations

import os
import socket
from typing import TYPE_CHECKING

from docdoc.cli.render import Rendering
from docdoc.runs.errors import RunStateUnavailableError
from docdoc.runs.identity import (
    configured_delivery_attempts,
    configured_delivery_backoff,
    configured_delivery_batch,
    configured_delivery_timeout,
    configured_lease,
    configured_maintenance_budget,
    configured_maintenance_interval,
    configured_max_attempts,
    configured_retention,
    configured_sweep_batch,
    delivery_allows_private,
)
from docdoc.runs.worker import Worker

if TYPE_CHECKING:
    import argparse

    from docdoc.cli.config import Settings

__all__ = ["run"]

#: How long to wait for the database to answer a connection attempt. The same
#: value `docdoc.api.app` uses, for the same reason: an untimed connect blocks
#: for the OS default, which is minutes.
CONNECT_TIMEOUT_SECONDS = 5


def _worker_id() -> str:
    """Host and pid, which is enough.

    Diagnostic only — nothing routes on it and worker liveness is the lease, not
    a registry (data-model.md). Under Docker the hostname is the container id,
    which is exactly what an operator reading a log wants to grep for.
    """
    return f"{socket.gethostname()}:{os.getpid()}"


def _deliverer(queue: object) -> object | None:
    """A deliverer, or ``None`` when nothing is configured to sign with.

    **No secrets file means no deliverer**, and that is the honest wiring rather
    than a degraded one: every delivery is signed (FR-055), so a deployment with
    nothing to sign with would attempt each one, fail it for a missing secret,
    and retire it at the attempt limit — six outbound requests per run to report
    a configuration problem. `None` performs no request at all (FR-064).
    """
    from docdoc.runs.delivery import PostgresDeliverer, SecretBook

    book = SecretBook.from_environment()
    if not book.by_digest:
        return None
    return PostgresDeliverer(
        execute=queue._execute,
        secrets=book,
        max_attempts=configured_delivery_attempts(),
        timeout_seconds=configured_delivery_timeout(),
        backoff_seconds=configured_delivery_backoff(),
        allow_private=delivery_allows_private(),
    )


def run(args: argparse.Namespace, settings: Settings) -> Rendering:
    """Claim and execute until signalled."""
    dsn = getattr(args, "run_database_url", None) or settings.run_database_url
    if not dsn:
        raise RunStateUnavailableError(
            "no run-state database configured; set DOCDOC_RUN_DATABASE_URL or pass "
            "--run-database-url"
        )
    if not settings.has_store:
        # A worker with no store would execute every run from scratch and write
        # nothing, so the next run over the same document would too. That is not
        # a degraded mode worth having: it is the reuse guarantee silently off.
        #
        # It is also worse than that here: the blobs a worker reads are written
        # by the API's submission route, so a worker with no store cannot reach
        # the document at all and would fail every run it claimed.
        raise RunStateUnavailableError(
            "no store configured; a worker needs DOCDOC_STORE_ROOT, DOCDOC_STORE_URL, "
            "--store, or --store-url, or it cannot reach the documents it claims"
        )

    try:
        import psycopg
    except ImportError as exc:
        raise RunStateUnavailableError(
            "psycopg is not installed; run state needs `pip install docdoc[postgres]`"
        ) from exc

    from docdoc.runs.observe import install_bridge
    from docdoc.runs.postgres import PostgresRunQueue
    from docdoc.telemetry import (
        ENDPOINT_ENV,
        HEADERS_ENV,
        bridge,
        configure_logging,
        parse_headers,
    )

    # **First**, because until this ran the worker logged nothing at all: every
    # `docdoc.*` record was INFO, and `docdoc.*` had no handler and inherited
    # `WARNING`. This is the process where `run.transition` happens — a claim, a
    # lease expiry, a redelivery — so it was the half of the deployment an
    # operator most needs to see and could not.
    configure_logging()

    # Before the loop starts, and only when both observer slots are empty
    # (research R4). The worker is the process where `run.transition` actually
    # happens — a claim, a lease expiry, a redelivery — so a deployment that
    # traced only the API would be tracing the half that never fails.
    endpoint = os.environ.get(ENDPOINT_ENV, "")
    headers = os.environ.get(HEADERS_ENV, "")
    install_bridge(
        lambda: bridge(endpoint=endpoint, headers=parse_headers(headers)), endpoint=endpoint
    )

    queue = PostgresRunQueue(lambda: psycopg.connect(dsn, connect_timeout=CONNECT_TIMEOUT_SECONDS))

    worker = Worker(
        # `connect_timeout` matters more here than on the API, which already sets
        # it. A black-holed database — a security group change, a failover that
        # left an address answering nothing — makes an untimed `connect` block
        # for the OS default, and in this process that hangs the claim loop, the
        # heartbeat thread, and the `/readyz` handler all at once. `/healthz`
        # keeps answering, because it touches nothing, so an orchestrator sees a
        # live process that is doing no work and reports nothing about why.
        queue=queue,
        blobs=settings.blobs(),
        store=settings.store(),
        registry=settings.registry(),
        adapter=settings.adapter(),
        worker_id=_worker_id(),
        lease=configured_lease(getattr(args, "lease_seconds", None)),
        max_attempts=configured_max_attempts(getattr(args, "max_attempts", None)),
        limits=settings.limits(),
        health_port=getattr(args, "health_port", None),
        # A worker executes whatever it claims, and what it claims belongs to
        # whichever tenant submitted it. Without this it would run every tenant's
        # document against the default tenant's namespace — finding no blob for
        # any of them, and writing artifacts where another tenant could reuse
        # them (FR-084, FR-086).
        stores_for=settings.stores_for,
        # **What makes "on a schedule" true** (FR-114, FR-117). Without these the
        # tick has nothing configured, so retention exists only as a command
        # somebody has to remember to run and a delivery waits for a `docdoc`
        # invocation that never comes. Both read the environment and both are
        # `None`/absent by default, so a Milestone 9 worker upgrades and sweeps
        # nothing, delivers nothing, and behaves exactly as it did.
        retention_period=configured_retention(),
        sweep_batch=configured_sweep_batch(),
        deliverer=_deliverer(queue),
        delivery_batch=configured_delivery_batch(),
        maintenance_interval_seconds=configured_maintenance_interval(),
        maintenance_budget_ms=configured_maintenance_budget(),
    )
    worker.install_signal_handlers()
    worker.run_forever()

    return Rendering(code=0, data={"worker": "stopped"}, lines=["stopped"])
