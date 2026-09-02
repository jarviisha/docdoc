"""Run identities and clock reads, in one module, on purpose.

**This is the only module in `docdoc.runs` permitted to import `uuid`, `time`,
`datetime`, `random`, or `secrets`** (FR-072).
`tests/unit/test_runs_clock_confinement.py` asserts it, so the rule is checked
rather than remembered.

The rule exists for two reasons, and the second is the load-bearing one.

**The determinism guard.** The kernel performs no clock, file, network, or random
access, enforced by an AST scan and a runtime audit hook. Milestone 9 is the
first work above the kernel that genuinely needs a clock, so the risk is drift: a
lease comparison written inline in `postgres.py` passes CI today and makes the
queue untestable at arbitrary times tomorrow. Nothing here is permitted to travel
downward — `pipeline` and everything below it stays a pure function of its
inputs, and the guard is neither relaxed nor granted an exemption (FR-073).

**Redelivery safety.** ADR-0013 §4 makes at-least-once delivery safe by observing
that re-executing a stage cannot produce a different answer. That is only true
while the stages below are deterministic. So confining the clock is not
housekeeping in service of a linter — it is what keeps a redelivered run from
disagreeing with the one it replaced.

The practical payoff is that every other function in this package takes `now` as
a parameter and is therefore a pure function of `(state, now)`: the claim policy,
the lease-expiry rule, and the state machine are all testable against an
in-memory fake at any instant, with no database and no sleeping.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

__all__ = [
    "DEFAULT_LEASE",
    "DEFAULT_RETENTION",
    "RunId",
    "deadline",
    "new_run_id",
    "now",
]

#: A run identity. Opaque, and never derived from content (FR-002).
RunId = uuid.UUID

#: How long a claim holds before another worker may take the run. Three heartbeat
#: periods, so a live worker has two chances to miss one before losing a run it
#: is still executing, and a *dead* worker's run waits at most this long to be
#: redelivered. Sized to the heartbeat rather than to the slowest document: a
#: lease measured in minutes would make every crash cost minutes of latency
#: (research R9).
DEFAULT_LEASE = timedelta(seconds=90)

#: How long a run row is kept. Nothing in Milestone 9 reads this — there is no
#: sweep and no `expired` state (FR-015). The column exists because adding it to
#: a populated table later is the migration problem `tenant_id` has, and
#: Milestone 10's retention work will act on it.
DEFAULT_RETENTION = timedelta(days=30)


def new_run_id() -> RunId:
    """A fresh, opaque run identity.

    `uuid4` rather than anything derived, and that is FR-002 rather than
    convenience. A derived-looking identifier that is *not* the artifact identity
    invites a caller to reason about it — to compare two, to infer that equal
    inputs give equal ids — and every such inference is wrong here. Two
    submissions of the same document are two runs and one result (ADR-0013 §1);
    an identifier that hid that would be hiding the distinction this layer
    exists to draw.
    """
    return uuid.uuid4()


def now() -> datetime:
    """The current instant, timezone-aware and in UTC.

    Aware rather than naive because it is compared against `timestamptz` values
    and against deadlines computed here. A naive datetime would compare fine in
    tests, in one timezone, until it did not.
    """
    return datetime.now(UTC)


def deadline(after: datetime, duration: timedelta) -> datetime:
    """`after + duration`, as one named operation.

    Trivial, and it exists so that lease and retention arithmetic has a single
    home rather than appearing as `now() + lease` at four call sites — three of
    which would eventually read a clock of their own to get `now`, which is the
    drift this module was written to prevent.
    """
    return after + duration
