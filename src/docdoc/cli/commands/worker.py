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
from docdoc.runs.identity import DEFAULT_LEASE
from docdoc.runs.worker import DEFAULT_MAX_ATTEMPTS, Worker

if TYPE_CHECKING:
    import argparse

    from docdoc.cli.config import Settings

__all__ = ["run"]


def _worker_id() -> str:
    """Host and pid, which is enough.

    Diagnostic only — nothing routes on it and worker liveness is the lease, not
    a registry (data-model.md). Under Docker the hostname is the container id,
    which is exactly what an operator reading a log wants to grep for.
    """
    return f"{socket.gethostname()}:{os.getpid()}"


def run(args: argparse.Namespace, settings: Settings) -> Rendering:
    """Claim and execute until signalled."""
    dsn = getattr(args, "run_database_url", None) or settings.run_database_url
    if not dsn:
        raise RunStateUnavailableError(
            "no run-state database configured; set DOCDOC_RUN_DATABASE_URL or pass "
            "--run-database-url"
        )
    if settings.store_root is None:
        # A worker with no store would execute every run from scratch and write
        # nothing, so the next run over the same document would too. That is not
        # a degraded mode worth having: it is the reuse guarantee silently off.
        raise RunStateUnavailableError(
            "no store configured; a worker needs DOCDOC_STORE_ROOT or --store, or "
            "every run pays for every stage again"
        )

    try:
        import psycopg
    except ImportError as exc:
        raise RunStateUnavailableError(
            "psycopg is not installed; run state needs `pip install docdoc[postgres]`"
        ) from exc

    from docdoc.artifacts import BlobStore
    from docdoc.runs.postgres import PostgresRunQueue

    lease = DEFAULT_LEASE
    if getattr(args, "lease_seconds", None):
        from datetime import timedelta

        lease = timedelta(seconds=args.lease_seconds)

    worker = Worker(
        queue=PostgresRunQueue(lambda: psycopg.connect(dsn)),
        blobs=BlobStore(settings.store_root),
        store=settings.store(),
        registry=settings.registry(),
        adapter=settings.adapter(),
        worker_id=_worker_id(),
        lease=lease,
        max_attempts=getattr(args, "max_attempts", None) or DEFAULT_MAX_ATTEMPTS,
        limits=settings.limits(),
    )
    worker.install_signal_handlers()
    worker.run_forever()

    return Rendering(code=0, data={"worker": "stopped"}, lines=["stopped"])
