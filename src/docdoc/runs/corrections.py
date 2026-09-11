"""Storage for a model this package does not own.

``Correction`` is Milestone 6's and is imported, not redefined (FR-077). It lives
in ``docdoc.evaluation`` and stays there: that layer's ``forbidden`` contract bars
``socket``, ``urllib``, ``http``, and ``docdoc.artifacts``, so a database-backed
store cannot live beside the model and weakening the contract to host one would
trade a machine-checked property for a convenience (specs/010 research R13).

**This is the one place Milestone 10's two halves touch.** `pinned_runs` is how
retention learns that a run carries a live correction and must not be removed
(FR-013). Naming it on a Protocol rather than leaving retention to join a table
is deliberate: the coupling is real, and a named method is where a reviewer can
see it.

**Every method takes `tenant_id`** (FR-079). A `run_id` is not a permission, and
scoping in the query rather than checking after the fetch means a caller cannot
forget a check that does not exist.

**Nothing here assigns, queues, or tracks a reviewer** (FR-083). The absent
methods are the contract: there is no `assign`, no `claim`, no `next_for`, and no
review state. Principle IX permits the model and forbids the platform, and the
deferred-technology list names "a full review UI" by name.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from docdoc.evaluation.corrections import Correction

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from datetime import datetime
    from uuid import UUID

__all__ = [
    "Correction",
    "CorrectionStore",
    "NullCorrectionStore",
    "PostgresCorrectionStore",
    "for_promotion",
]


class CorrectionStore(Protocol):
    """Record, read back, and answer the one question retention asks."""

    def record(
        self,
        correction: Correction,
        *,
        tenant_id: str,
        run_id: UUID,
        at: datetime,
        expires_at: datetime,
    ) -> UUID:
        """Store one correction whole and return its identity."""
        ...

    def for_run(self, *, tenant_id: str, run_id: UUID) -> tuple[Correction, ...]:
        """This tenant's corrections against this run, and nobody else's."""
        ...

    def pinned_runs(self, run_ids: Sequence[UUID], *, at: datetime) -> frozenset[UUID]:
        """Which of these runs carry a correction that has not itself expired."""
        ...


@dataclass(frozen=True)
class NullCorrectionStore:
    """Nothing is pinned, because nothing can be.

    Correct on its own terms for a deployment that has configured no correction
    retention and recorded no corrections, and it is what `retention.sweep`
    defaults to — which is why shipping the operational half without this one
    needed no branch in the sweep.
    """

    def record(self, *_: Any, **__: Any) -> UUID:
        from docdoc.runs.errors import RunError

        raise RunError("this deployment records no corrections")

    def for_run(self, *, tenant_id: str, run_id: UUID) -> tuple[Correction, ...]:
        return ()

    def pinned_runs(self, run_ids: Sequence[UUID], *, at: datetime) -> frozenset[UUID]:
        return frozenset()


@dataclass
class PostgresCorrectionStore:
    """The table, and the three columns lifted out of the payload.

    ``execute`` is `PostgresRunQueue`'s, passed in rather than reached for, so
    this module opens no connection of its own and inherits that class's rule
    that no driver exception escapes.
    """

    execute: Callable[..., Any]

    def record(
        self,
        correction: Correction,
        *,
        tenant_id: str,
        run_id: UUID,
        at: datetime,
        expires_at: datetime,
    ) -> UUID:
        """Store the `Correction` **whole**, as `jsonb`.

        The three lifted columns — `run_id`, `processing_id`, `field_path` — are
        lifted because they are *queried*, not because they matter more. Storing
        the model whole is what keeps FR-077's "imported and never redefined"
        true at the storage layer as well: a schema that enumerated the seven
        fields would be a second definition of the model, and the two would drift
        the first time Milestone 6's changed.

        **It alters nothing it annotates** (FR-078). No artifact is written, no
        result is edited, and `processing_id` is copied rather than recomputed —
        a correction that changed the thing it described would make the recorded
        pipeline output a function of who reviewed it.
        """
        from docdoc.runs.identity import new_correction_id

        correction_id = new_correction_id()
        self.execute(
            "INSERT INTO corrections (correction_id, tenant_id, run_id, processing_id, "
            "field_path, payload, created_at, expires_at) "
            "VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, %s)",
            (
                correction_id,
                tenant_id,
                run_id,
                correction.report_id,
                correction.field_path,
                correction.model_dump_json(),
                at,
                expires_at,
            ),
        )
        return correction_id

    def for_run(self, *, tenant_id: str, run_id: UUID) -> tuple[Correction, ...]:
        rows = self.execute(
            "SELECT payload FROM corrections WHERE tenant_id = %s AND run_id = %s "
            "ORDER BY created_at",
            (tenant_id, run_id),
            fetch="all",
        )
        return tuple(_correction_from(row["payload"]) for row in rows or ())

    def pinned_runs(self, run_ids: Sequence[UUID], *, at: datetime) -> frozenset[UUID]:
        """**The one method retention calls** (FR-013).

        Not tenant-scoped, and that is the one deliberate exception to this
        module's own rule. The sweep is not a caller acting on behalf of a
        tenant; it is the deployment asking "may I delete these rows", and an
        answer scoped to one tenant would let a correction owned by another fail
        to protect a run it pins. It returns identities the caller already holds
        and discloses nothing it did not already have.

        Empty input short-circuits: `IN ()` is a syntax error in Postgres, and a
        sweep whose first batch is empty is the ordinary case.
        """
        if not run_ids:
            return frozenset()

        rows = self.execute(
            "SELECT DISTINCT run_id FROM corrections WHERE run_id = ANY(%s) AND expires_at > %s",
            (list(run_ids), at),
            fetch="all",
        )
        return frozenset(row["run_id"] for row in rows or ())


def _correction_from(payload: Any) -> Correction:
    """One stored row, back into the model that owns it.

    `payload` arrives from `jsonb` already decoded on the Postgres path and as
    text from anything that stored a string; both are accepted because a store
    that raised on one of them would fail on exactly one driver configuration.
    """
    data = json.loads(payload) if isinstance(payload, str | bytes) else payload
    return Correction.model_validate(data)


def for_promotion(corrections: Sequence[Correction]) -> tuple[Correction, ...]:
    """The corrections, in the form the existing promotion path already accepts.

    **Deliberately a projection and not a transformation** (FR-081, T150).
    `docdoc.evaluation.corrections.promote` takes `Correction` instances and
    returns a new golden set with a new `golden_set_id`; what was missing was not
    a converter but a way to *get* the corrections out of the store, which
    `for_run` now provides. So this sorts them into a stable order and hands them
    over.

    A richer export was the alternative and it is the wrong shape: a second
    representation of a correction is a second thing to keep in step with
    Milestone 6's model, and the whole point of FR-077 is that there is one.

    **It promotes nothing.** Promotion is a separate, explicit act producing a
    new `golden_set_id` (FR-080), so that reports either side of it are not
    comparable without the difference being visible. A correction that entered a
    dataset by being recorded would move every historical number and explain
    none of them — which is what SC-018 measures.
    """
    return tuple(sorted(corrections, key=lambda c: (c.field_path, c.timestamp, c.annotator)))
