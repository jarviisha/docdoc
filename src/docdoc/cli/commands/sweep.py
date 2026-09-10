"""``docdoc sweep`` — remove expired runs and the content only they held.

**The command exists even though the worker sweeps automatically** (T046a), for
two reasons that are different in kind. An operator sometimes needs a sweep *now*
— after lowering a retention period, or before a migration — and a deployment
that runs no worker (a synchronous-only one) has nothing that would otherwise
sweep at all (FR-117).

**Retention is off unless configured.** With no period set this reports zero and
removes nothing, which is FR-014 and the same default every capability in this
milestone has: an upgrade changes nothing until somebody asks.

**Nothing here decides what to delete.** That is `runs.retention`, and it is a
set difference over run rows — see its module docstring, and ADR-0015 §1 for why
the obvious alternative deletes every artifact the command line ever wrote.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docdoc.cli.render import Rendering
from docdoc.runs import retention
from docdoc.runs.identity import configured_retention, configured_sweep_batch
from docdoc.runs.identity import now as clock

if TYPE_CHECKING:
    import argparse

    from docdoc.cli.config import Settings

__all__ = ["EXIT_NOT_CONFIGURED", "run"]

#: No retention period is configured, so there is nothing this command can do.
#: A real answer rather than a failure — the same shape `migrate --check` uses
#: for "pending" — because an operator who has not opted in should be told that
#: rather than shown a traceback or a silent zero.
EXIT_NOT_CONFIGURED = 1


def run(args: argparse.Namespace, settings: Settings) -> Rendering:
    """Sweep one batch, or as many as it takes with ``--all``."""

    period = configured_retention(getattr(args, "retention_days", None))
    if period is None:
        return Rendering(
            code=EXIT_NOT_CONFIGURED,
            data={"swept": False, "reason": "no_retention_period"},
            lines=[
                "no retention period configured; nothing was swept. Set "
                "DOCDOC_RUN_RETENTION_DAYS or pass --retention-days"
            ],
        )

    batch = configured_sweep_batch(getattr(args, "batch", None))
    queue = settings.run_queue(getattr(args, "run_database_url", None))
    now = clock()

    def stores_for(tenant_id: str) -> retention.Stores:
        artifacts, blobs = settings.stores_for(tenant_id)
        return retention.Stores(artifacts=artifacts, blobs=blobs)

    report = retention.sweep(queue, stores_for, now=now, batch=batch)
    # `--all` keeps going while a pass is still finding work. Bounded per pass
    # rather than per invocation (FR-015): the transaction stays short, and an
    # operator who wants a year of rows gone in one sitting says so.
    if getattr(args, "all", False):
        while report.runs and not report.degraded:
            more = retention.sweep(queue, stores_for, now=now, batch=batch)
            if not more.runs:
                break
            report = report.merged(more)

    return Rendering(
        code=0,
        data={
            "runs": report.runs,
            "artifacts": report.artifacts,
            "blobs": report.blobs,
            "pinned": report.pinned,
            "degraded": report.degraded,
        },
        lines=(
            ["the store could not be reached; nothing was removed"]
            if report.degraded
            else [
                f"removed {report.runs} run(s), {report.artifacts} artifact(s), "
                f"{report.blobs} blob(s); {report.pinned} pinned by a correction"
            ]
        ),
    )
