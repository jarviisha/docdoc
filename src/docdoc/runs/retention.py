"""The sweep and the erasure, and the set difference that makes both safe.

**The deletion set is a difference, never a complement** (ADR-0015 §1):

    candidates = union of stage_outcomes[].artifact_id over the runs being removed
    survivors  = union of stage_outcomes[].artifact_id over that tenant's remaining runs
    delete       candidates - survivors

An artifact becomes a candidate **only by having been named by a run that is
being removed**. That is the whole safety property of this module, and the
rejected form is one word away in English: "delete every artifact no surviving
run references" is a *complement*, and it deletes every artifact ``docdoc
extract`` ever wrote from the command line, everything a library caller produced,
and everything the recorder wrote — because none of those has a run row.
``tests/unit/test_sweep_is_a_difference.py`` exists to fail if anyone converts
one into the other.

**Survivorship is read, not derived.** A run row's ``stage_outcomes`` already
carries an ``artifact_id`` per stage, so nothing here needs a parser, a schema
registry, an adapter, or an options hash. Re-deriving the chain would need the
parser version in effect when the run executed, which a reconfigured deployment
no longer has — it would silently compute a different set and miss artifacts,
which is the worst available failure in a routine whose job is removal.

**Order is fixed**: artifacts and blobs, then the tombstone, then the run row. A
crash between any two steps leaves the run present with its content partly gone,
and the next sweep recomputes the difference from what remains and finishes. The
reverse order loses the list of what to delete.

**Nothing here reads a clock.** ``now`` is a parameter, as everywhere else in
this package, so a sweep is a pure function of ``(state, now)`` and testable at
an arbitrary instant (FR-096a).
"""

from __future__ import annotations

import logging
from contextlib import suppress
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from docdoc.artifacts.errors import ArtifactError
from docdoc.runs.errors import RetentionError, RunError

if TYPE_CHECKING:
    from collections.abc import Sequence
    from datetime import datetime
    from uuid import UUID

    from docdoc.runs.model import Run
    from docdoc.runs.queue import RunQueue

__all__ = [
    "POLICY_ERASE_DOCUMENT",
    "POLICY_ERASE_TENANT",
    "POLICY_RETENTION",
    "Stores",
    "SweepReport",
    "erase",
    "sweep",
]

_logger = logging.getLogger("docdoc.runs")

#: What goes in a tombstone's ``policy`` column. These three strings are the only
#: record of *why* a run was removed, because `RunStatus` gains no member to
#: carry it (FR-004a).
POLICY_RETENTION = "retention"
POLICY_ERASE_TENANT = "erasure:tenant"
POLICY_ERASE_DOCUMENT = "erasure:document"


class _Store(Protocol):
    """The deletion half of a store, which is all this module uses."""

    def delete(self, identity: str) -> bool: ...

    def delete_prefix(self, *, allow_store_root: bool = False) -> int: ...


@dataclass(frozen=True)
class Stores:
    """The two stores a tenant's content lives in.

    Built per tenant by the caller, because a store instance *is* a tenant
    (ADR-0014 §3): ``tenant_id`` is a constructor argument, so this module never
    passes one to a delete call and cannot pass the wrong one.
    """

    artifacts: _Store
    blobs: _Store


@dataclass(frozen=True)
class SweepReport:
    """Counts by kind, and **no identifiers of what was removed** (FR-012).

    A report listing what it erased is a way of retaining it. What an operator
    needs is how much went; what an auditor needs is the tombstone, which is
    scoped to the tenant entitled to read it.
    """

    runs: int = 0
    artifacts: int = 0
    blobs: int = 0
    #: Rows removed from the four tables this milestone adds beside `runs`
    #: (FR-086). Only an erasure touches these: a retention sweep removes one
    #: run, and a tenant's callbacks and counters are not that run's.
    rows: int = 0
    pinned: int = 0
    #: Populated when the store could not be reached. The sweep removed nothing
    #: in that case, including no run rows.
    degraded: bool = False

    def merged(self, other: SweepReport) -> SweepReport:
        return SweepReport(
            runs=self.runs + other.runs,
            artifacts=self.artifacts + other.artifacts,
            blobs=self.blobs + other.blobs,
            rows=self.rows + other.rows,
            pinned=self.pinned + other.pinned,
            degraded=self.degraded or other.degraded,
        )


class CorrectionStore(Protocol):
    """What retention needs to know about corrections, and nothing else.

    One method, named here rather than left as a table join, because the coupling
    is real and a reviewer should be able to see it: this is the single place
    Milestone 10's operational half touches its product half (FR-013). A policy
    that deleted a result somebody corrected is a data-loss bug that appears only
    once both features exist.

    **Narrower than `corrections.CorrectionStore` on purpose.** That one has
    three methods; this one has the single method the sweep calls, so the sweep
    cannot record a correction, cannot read one, and cannot be given something
    that does. `PostgresCorrectionStore` satisfies both, structurally, with no
    inheritance to declare.
    """

    def pinned_runs(self, run_ids: Sequence[UUID], *, at: datetime) -> frozenset[UUID]:
        """Which of these runs carry a correction that has not itself expired."""
        ...


@dataclass(frozen=True)
class NullCorrectionStore:
    """Nothing is pinned, because nothing can be.

    The default, and correct on its own terms for a deployment that records no
    corrections: with none recorded, no run carries one. That is what let the
    operational half of this milestone ship without a branch here.
    """

    def pinned_runs(self, run_ids: Sequence[UUID], *, at: datetime) -> frozenset[UUID]:
        return frozenset()


def sweep(
    queue: RunQueue,
    stores_for: Any,
    *,
    now: datetime,
    batch: int,
    corrections: CorrectionStore | None = None,
    policy: str = POLICY_RETENTION,
) -> SweepReport:
    """Remove one batch of expired runs and the content only they held.

    ``stores_for`` is a callable taking a ``tenant_id`` and returning `Stores`,
    because a batch may span tenants and a store instance is bound to one.

    Bounded by ``batch`` (FR-015) and idempotent (FR-002): a second call over the
    same state removes nothing, and a call interrupted at any step is completed
    by the next one.
    """
    # Two passes, and the second is what keeps a batch from stalling.
    #
    # Asking once and filtering in Python would spend the whole `batch` on runs a
    # correction pins, so a tenant with many corrected results would sweep
    # nothing while reporting that it looked (FR-013 against FR-015). So the
    # pinned set is fed back through `exclude` and a second, equally bounded
    # question is asked -- which is what that parameter is for.
    pinner = corrections or NullCorrectionStore()
    first = queue.expiring(now=now, limit=batch)
    if not first:
        return SweepReport()

    pinned = pinner.pinned_runs([run.run_id for run in first], at=now)
    removable = [run for run in first if run.run_id not in pinned]
    if pinned and len(removable) < batch:
        refill = queue.expiring(now=now, limit=batch - len(removable), exclude=pinned)
        still_pinned = pinner.pinned_runs([run.run_id for run in refill], at=now)
        removable += [run for run in refill if run.run_id not in still_pinned]

    report = SweepReport(pinned=len(pinned))
    if not removable:
        return report

    by_tenant: dict[str, list[Run]] = {}
    for run in removable:
        by_tenant.setdefault(run.tenant_id, []).append(run)

    for tenant_id, runs in by_tenant.items():
        report = report.merged(
            _remove(queue, stores_for(tenant_id), tenant_id, runs, now=now, policy=policy)
        )
    return report


def erase(
    queue: RunQueue,
    stores: Stores,
    *,
    tenant_id: str,
    now: datetime,
    blob_id: str | None = None,
    limit: int = 10_000,
    allow_store_root: bool = False,
    is_default_tenant: bool = False,
) -> SweepReport:
    """Remove a tenant's, or one document's, content on request.

    **Idempotent** (FR-009). A second call finds nothing and succeeds, and
    erasing a tenant that never existed succeeds having removed nothing — because
    an operator running this twice under time pressure is the normal case, not the
    exceptional one.

    **In-flight runs are cancelled first** (FR-010). Erasure must never leave a
    worker writing artifacts into a namespace that has just been removed, and a
    queued run is still the customer's data.

    **Which path this takes is the whole of ADR-0015 §5.**

    A *named* tenant uses `delete_prefix`, which Milestone 9 made affordable by
    putting the tenant above the store's fan-out (ADR-0014 §5) — one operation
    instead of a scan.

    The *default* tenant does not, and cannot. Its namespace is the store root, so
    the prefix path would remove everything written before authentication was
    enabled and everything ``docdoc extract`` ever wrote — none of which belongs
    to the customer being erased. It falls back to the set difference: that
    tenant's run-derived content, and nothing else. Emptying the root outright is
    `allow_store_root`, which one command-line flag passes and no HTTP route can.

    A *document* erasure is always the difference, whichever tenant owns it: a
    prefix cannot express "one blob".
    """
    runs = queue.runs_for(tenant_id, blob_id=blob_id, limit=limit)

    # First, so that nothing is executing into a namespace about to disappear.
    # Cancelling a queued run stops it for good; cancelling a running one is a
    # request observed at the next stage boundary, which is Milestone 9's
    # semantics and unchanged here.
    for run in runs:
        if not run.is_terminal:
            with suppress(RunError):
                queue.cancel(run.run_id, tenant_id, now=now)

    policy = POLICY_ERASE_DOCUMENT if blob_id is not None else POLICY_ERASE_TENANT
    use_prefix = blob_id is None and (not is_default_tenant or allow_store_root)

    if not use_prefix:
        report = _remove(queue, stores, tenant_id, runs, now=now, policy=policy)
        return report.merged(_purge(queue, tenant_id, blob_id=blob_id, runs=runs))

    try:
        artifacts = stores.artifacts.delete_prefix(allow_store_root=allow_store_root)
        blobs = stores.blobs.delete_prefix(allow_store_root=allow_store_root)
    except ArtifactError as error:
        if error.reason == "store_root_refused":
            # The guard fired. Raised rather than degraded, because this is a
            # refusal to do something destructive and not a store that went away.
            raise RetentionError(str(error)) from error
        return _degraded(error, tenant_id)

    removed = queue.entomb(runs, now=now, policy=policy)
    report = SweepReport(runs=removed, artifacts=artifacts, blobs=blobs).merged(
        _purge(queue, tenant_id, blob_id=blob_id, runs=list(runs))
    )
    _log(report, tenant_id=tenant_id, policy=policy)
    return report


def _purge(
    queue: RunQueue,
    tenant_id: str,
    *,
    blob_id: str | None,
    runs: Sequence[Run] = (),
) -> SweepReport:
    """Remove the rows the other four tables hold for this tenant (FR-086).

    **On both erasure paths, and on neither sweep.** An earlier version called
    this only from the prefix branch, so erasing the *default* tenant -- the one
    case ADR-0015 §5 forces onto the set-difference path -- left its corrections,
    callbacks, deliveries, and counters behind. That is the failure spec.md names:
    answering "erased" about data that is still there.

    A **retention sweep** must not do this and does not. A sweep removes one run
    that aged out; a tenant's callbacks and counters are not that run's, and
    dropping them would make retention quietly deconfigure a customer.

    Erasing **one document** is narrower still: only the removed runs' rows go,
    because the tenant is not being erased. Scoped by `run_id` rather than by
    tenant for exactly that reason.
    """
    if blob_id is not None:
        # Document erasure. The tenant keeps its callbacks and its counters; what
        # goes is what belonged to the runs over that document, and those rows
        # are keyed by `run_id`.
        counts = queue.purge_rows_for([run.run_id for run in runs])
    else:
        counts = queue.purge_tenant_rows(tenant_id)
    return SweepReport(rows=sum(counts.values()))


def _remove(
    queue: RunQueue,
    stores: Stores,
    tenant_id: str,
    runs: Sequence[Run],
    *,
    now: datetime,
    policy: str,
) -> SweepReport:
    """The difference, applied in the one order that is safe to interrupt."""
    doomed = {run.run_id for run in runs}
    kept_artifacts, kept_blobs = queue.retained_ids_for(tenant_id, excluding=doomed)

    # The difference, both halves. A blob survives on exactly the terms an
    # artifact does: two runs over the same document share one, and that is the
    # most ordinary thing a tenant does.
    artifact_ids = _artifact_ids(runs) - kept_artifacts
    blob_ids = frozenset(run.blob_id for run in runs) - kept_blobs

    try:
        artifacts = sum(stores.artifacts.delete(identity) for identity in artifact_ids)
        blobs = sum(stores.blobs.delete(blob_id) for blob_id in blob_ids)
    except ArtifactError as error:
        # Nothing is entombed and no row is removed. The run row holds the
        # `stage_outcomes` naming what still needs deleting, so removing it while
        # the content survives loses the only record of the work (ADR-0015 §3).
        return _degraded(error, tenant_id)

    removed = queue.entomb(runs, now=now, policy=policy)
    report = SweepReport(runs=removed, artifacts=artifacts, blobs=blobs)
    _log(report, tenant_id=tenant_id, policy=policy)
    return report


def _artifact_ids(runs: Sequence[Run]) -> frozenset[str]:
    """Read, not derived (ADR-0015 §2)."""
    return frozenset(
        outcome.artifact_id
        for run in runs
        for outcome in run.stage_outcomes
        if outcome.artifact_id is not None
    )


def _degraded(error: ArtifactError, tenant_id: str) -> SweepReport:
    _logger.warning(
        "retention degraded: the store could not be reached, so nothing was removed",
        extra={
            "docdoc": {
                "event": "retention.degraded",
                "tenant_id": tenant_id,
                "reason": error.reason,
            }
        },
    )
    return SweepReport(degraded=True)


def _log(report: SweepReport, *, tenant_id: str, policy: str) -> None:
    """One structured event carrying counts and no content (FR-012)."""
    _logger.info(
        "retention.swept",
        extra={
            "docdoc": {
                "event": "retention.swept",
                "tenant_id": tenant_id,
                "policy": policy,
                "runs": report.runs,
                "artifacts": report.artifacts,
                "blobs": report.blobs,
                "pinned": report.pinned,
            }
        },
    )
