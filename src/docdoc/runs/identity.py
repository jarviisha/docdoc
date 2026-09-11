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

import os
import secrets
import time
import uuid
from datetime import UTC, datetime, timedelta

__all__ = [
    "CORRECTION_RETENTION_DAYS_ENV",
    "CREDENTIAL_TTL_ENV",
    "DEFAULT_CREDENTIAL_TTL_SECONDS",
    "DEFAULT_DELIVERY_ATTEMPTS",
    "DEFAULT_DELIVERY_BACKOFF_SECONDS",
    "DEFAULT_DELIVERY_BATCH",
    "DEFAULT_DELIVERY_TIMEOUT_SECONDS",
    "DEFAULT_FAIRNESS_WINDOW",
    "DEFAULT_LEASE",
    "DEFAULT_MAX_ATTEMPTS",
    "DEFAULT_RETENTION",
    "DEFAULT_STARVATION",
    "DEFAULT_SWEEP_BATCH",
    "DELIVERY_ALLOW_PRIVATE_ENV",
    "DELIVERY_ATTEMPTS_ENV",
    "DELIVERY_BACKOFF_ENV",
    "DELIVERY_BATCH_ENV",
    "DELIVERY_SECRETS_FILE_ENV",
    "DELIVERY_TIMEOUT_ENV",
    "KEY_PREFIX",
    "LEASE_SECONDS_ENV",
    "LIMIT_RETENTION",
    "LIMIT_WINDOWS",
    "MAINTENANCE_BUDGET_ENV",
    "MAINTENANCE_INTERVAL_ENV",
    "MAX_ATTEMPTS_ENV",
    "RETENTION_DAYS_ENV",
    "STARVATION_SECONDS_ENV",
    "SWEEP_BATCH_ENV",
    "RunId",
    "backoff",
    "configured_correction_retention",
    "configured_credential_ttl",
    "configured_delivery_attempts",
    "configured_delivery_backoff",
    "configured_delivery_batch",
    "configured_delivery_timeout",
    "configured_lease",
    "configured_maintenance_budget",
    "configured_maintenance_interval",
    "configured_max_attempts",
    "configured_retention",
    "configured_starvation",
    "configured_sweep_batch",
    "deadline",
    "delivery_allows_private",
    "monotonic_ms",
    "new_correction_id",
    "new_credential_id",
    "new_delivery_id",
    "new_erasure_id",
    "new_key_secret",
    "new_run_id",
    "now",
    "window_start",
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

#: How many times a run may be claimed before it is abandoned. Three, because
#: the failure this bounds is the poison document, and a document that terminates
#: three workers will terminate thirty (research R9).
DEFAULT_MAX_ATTEMPTS = 3

#: The two knobs a deployment may move, and the reason they are read *here*.
#:
#: Both are worker settings, and the worker is a subcommand of the CLI while the
#: names are documented as part of the service's configuration. `docdoc.api` and
#: `docdoc.cli` are declared independent of each other, so a precedence rule
#: living in either is one the other cannot call — and two copies of a
#: precedence rule is how the two eventually disagree. This module already owns
#: both defaults, so it owns the resolution as well. `docdoc.api.settings`
#: re-exports these under the names the HTTP layer's readers look for.
LEASE_SECONDS_ENV = "DOCDOC_RUN_LEASE_SECONDS"
MAX_ATTEMPTS_ENV = "DOCDOC_RUN_MAX_ATTEMPTS"


def configured_lease(explicit: int | None = None) -> timedelta:
    """The claim duration: explicit argument, then environment, then default.

    The precedence FR-083 requires of every setting, in one place rather than at
    each call site that needs it.

    **An explicit value is validated; an environment one is not**, and the
    asymmetry is deliberate. A malformed variable falls back to the default
    because a worker that dies over a typo in a tuning knob turns a cosmetic
    mistake into an outage. A flag typed on the command line is different: the
    operator is present, is watching, and silently getting the default is the one
    outcome that wastes their time. `--lease-seconds 0` used to do exactly that.
    """
    if explicit is not None:
        return timedelta(seconds=_demand_positive(explicit, "--lease-seconds"))
    seconds = _positive_int(os.environ.get(LEASE_SECONDS_ENV, ""))
    return DEFAULT_LEASE if seconds is None else timedelta(seconds=seconds)


def configured_max_attempts(explicit: int | None = None) -> int:
    """How many claims a run gets before abandonment. Same precedence.

    Validated for a reason worse than a wasted afternoon. `--max-attempts -1`
    passed straight through, and the claim requires ``attempts < max_attempts``
    while the sweep abandons at ``attempts >= max_attempts`` — so a negative
    value makes *every queued run* match the sweep and nothing match the claim.
    One cycle abandons the entire backlog as `RunAbandonedError`, terminally, and
    the word sends the operator to look at documents that are fine.
    """
    if explicit is not None:
        return _demand_positive(explicit, "--max-attempts")
    configured = _positive_int(os.environ.get(MAX_ATTEMPTS_ENV, ""))
    return DEFAULT_MAX_ATTEMPTS if configured is None else configured


def _demand_positive(value: int, flag: str) -> int:
    """An explicitly-given tuning value, or a refusal naming the flag."""
    if value <= 0:
        raise ValueError(
            f"{flag} must be a positive integer, not {value}. A lease of zero "
            "expires before it is taken and a negative attempt limit abandons "
            "every queued run on the first claim cycle"
        )
    return value


def _positive_int(raw: str) -> int | None:
    """A positive integer, or ``None`` for absent **and** for unreadable.

    A malformed value falls back to the default rather than refusing to start. A
    worker that dies over a typo in a tuning knob turns a cosmetic mistake into
    an outage, and neither knob can change what a run produces — which is the
    distinction `Limits` does not get to make and this one does.
    """
    text = raw.strip()
    if not text:
        return None
    try:
        value = int(text)
    except ValueError:
        return None
    return value if value > 0 else None


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


# -- Milestone 10 ------------------------------------------------------------
#
# Seven modules arrive in this package with this milestone -- `retention`,
# `keys`, `limits`, `delivery`, `routing`, `corrections`, `maintenance` -- and
# every one of them wants an identifier, an instant, or a random secret.
#
# `tests/unit/test_runs_clock_confinement.py` scans **every** module here and
# permits exactly one to import `uuid`, `time`, `datetime`, `random`, or
# `secrets`. The tempting way to keep it green is to add names to its `PERMITTED`
# set. That is the change FR-096a forbids by name, and the test's own docstring
# records why: an earlier version was silenced by an alias written to avoid a
# false positive, "which is the precise failure mode a guard that cannot see
# provenance will always have".
#
# So the allocators live here and the modules take what they need as parameters,
# exactly as `queue.py` already does. The payoff is the same one Milestone 9
# bought: a sweep, a limiter, and a delivery schedule are pure functions of
# `(state, now)` and testable at an arbitrary instant with no database.

#: Marks an issued credential as docdoc's when it turns up somewhere it should
#: not be -- a log aggregator, a bug report, a screenshot. It is a label and not
#: a secret; the entropy is in what follows it.
KEY_PREFIX = "ddk_"

#: How many random bytes an issued credential carries. 32 bytes is 256 bits,
#: which is the size of the digest it is stored under -- more would be entropy
#: the storage cannot distinguish, less would be a weaker credential than the
#: hash implies.
_KEY_BYTES = 32


def new_credential_id() -> uuid.UUID:
    """A fresh credential identity, which outlives the credential itself.

    Never reused, including after revocation (ADR-0016 §2): a revoked row stays,
    and an identifier that came back would make an audit trail ambiguous about
    which key an entry describes.
    """
    return uuid.uuid4()


def new_delivery_id() -> uuid.UUID:
    """A fresh delivery identity, allocated **once** per run.

    This is the value a receiver deduplicates on (FR-056), and it is stable
    across every retry because it is the delivery row's primary key. Allocating
    one per *attempt* would turn at-least-once delivery into at-least-once
    processing on the receiver's side -- the one thing at-least-once is meant to
    let them avoid.
    """
    return uuid.uuid4()


def new_correction_id() -> uuid.UUID:
    """A fresh correction identity."""
    return uuid.uuid4()


def new_erasure_id() -> uuid.UUID:
    """A fresh identity for one erasure request, so a caller can refer to it."""
    return uuid.uuid4()


def new_key_secret() -> str:
    """A fresh credential, in plaintext, returned exactly once (FR-025).

    `secrets` and not `random`: this is the only value in the project whose
    predictability would be a vulnerability rather than a nuisance.

    The caller is responsible for handing this to the requester and then
    forgetting it. Nothing stores it -- what goes in the table is
    ``sha256(key)``, which is what makes a leaked key file not immediately a set
    of working credentials.
    """
    return f"{KEY_PREFIX}{secrets.token_urlsafe(_KEY_BYTES)}"


def window_start(at: datetime, window: timedelta) -> datetime:
    """The start of the fixed window `at` falls in.

    Fixed windows rather than sliding ones (research R9): one row per tenant per
    kind per window, incremented by a single statement. A sliding window needs a
    row per submission -- making the limiter the largest table in this database --
    or a background decay process, which is the fifth process FR-116 forbids.

    **The cost is a boundary burst**: a tenant may issue up to twice its limit
    across a boundary. That is documented rather than engineered away, because
    the bound that actually protects a deployment is the worker pool size and it
    is unaffected.

    Takes `at` rather than reading a clock, so a limiter is testable at an
    arbitrary instant -- which is the whole reason this module exists.
    """
    if window <= timedelta(0):
        raise ValueError("a limit window must be a positive duration")
    epoch = datetime(1970, 1, 1, tzinfo=UTC)
    elapsed = (at - epoch) // window
    return epoch + elapsed * window


#: How many runs one sweep invocation removes. 500 clears a small deployment's
#: year of rows in minutes of ticks and holds a transaction for a bounded time
#: (research R15). The bound is what makes FR-015 true: a deployment that ran for
#: a year before retention landed makes progress rather than attempting one
#: unbounded delete.
DEFAULT_SWEEP_BATCH = 500

#: Unset means **nothing is swept** (FR-014). Retention is off by default like
#: every other capability in this milestone, and a deployment that configures no
#: period behaves exactly as Milestone 9 did.
RETENTION_DAYS_ENV = "DOCDOC_RUN_RETENTION_DAYS"
SWEEP_BATCH_ENV = "DOCDOC_RUN_SWEEP_BATCH"


def configured_retention(explicit: int | None = None) -> timedelta | None:
    """How long a run is kept: explicit, then environment, then **nothing**.

    `None` is a real answer and the default one. Every other setting in this
    module falls back to a value; this one falls back to "do not sweep", because
    a default retention period would delete a deployment's history on the first
    tick after an upgrade — the one thing FR-101 forbids of every capability
    here.

    `DEFAULT_RETENTION` still exists and is still what a run's `expires_at` is
    stamped with at creation. That column has been written since Milestone 9 and
    reading it is a separate decision from writing it, which is exactly the
    distinction this function keeps.
    """
    if explicit is not None:
        return timedelta(days=_demand_positive(explicit, "--retention-days"))
    days = _positive_int(os.environ.get(RETENTION_DAYS_ENV, ""))
    return None if days is None else timedelta(days=days)


def configured_sweep_batch(explicit: int | None = None) -> int:
    """How many runs one invocation removes. Same precedence as the lease."""
    if explicit is not None:
        return _demand_positive(explicit, "--batch")
    return _positive_int(os.environ.get(SWEEP_BATCH_ENV, "")) or DEFAULT_SWEEP_BATCH


def monotonic_ms() -> float:
    """Milliseconds on a monotonic clock, for bounding a maintenance tick.

    Monotonic and not wall-clock: a tick's budget must not be lengthened by an
    NTP correction or ended early by one, and the answer to "how long have I
    been going" is the one question `now()` cannot be used for.

    Here rather than in `maintenance.py` because this module is the only one in
    the package permitted to reach an ambient source, and the guard that enforces
    that must not be widened (FR-096a).
    """
    return time.monotonic() * 1000


#: How long a credential resolution is cached, and therefore **the propagation
#: bound for a revocation** (FR-028). The operator documentation states it as a
#: number, because "immediately" is not a property anybody can test.
DEFAULT_CREDENTIAL_TTL_SECONDS = 30
CREDENTIAL_TTL_ENV = "DOCDOC_RUN_CREDENTIAL_TTL_SECONDS"


def configured_credential_ttl(explicit: int | None = None) -> int:
    """The cache lifetime: explicit, then environment, then 30 seconds.

    Unlike retention, this has a default and it is deliberate. Retention off by
    default means "remove nothing", which is safe; a credential cache off by
    default would mean a database query per authenticated request, which is a
    much larger change than a deployment upgrading asked for.
    """
    if explicit is not None:
        return _demand_positive(explicit, "--credential-ttl-seconds")
    return _positive_int(os.environ.get(CREDENTIAL_TTL_ENV, "")) or DEFAULT_CREDENTIAL_TTL_SECONDS


#: How long a **correction** is kept, which is configured independently of how
#: long a run is (FR-013). Unset means corrections are kept for as long as their
#: runs are — the same "configure nothing and nothing changes" default retention
#: itself has.
#:
#: Where the two disagree, the longer wins: a run whose result carries a live
#: correction is not swept, because deleting a result somebody reviewed is the
#: data-loss bug that appears only once both halves of this milestone exist.
CORRECTION_RETENTION_DAYS_ENV = "DOCDOC_CORRECTION_RETENTION_DAYS"


def configured_correction_retention(explicit: int | None = None) -> timedelta | None:
    """How long a correction is kept: explicit, then environment, then ``None``.

    ``None`` means "as long as the run", which the caller expresses by using the
    run's own ``expires_at``. It is not "for ever" and it is not "immediately".
    """
    if explicit is not None:
        return timedelta(days=_demand_positive(explicit, "--correction-retention-days"))
    days = _positive_int(os.environ.get(CORRECTION_RETENTION_DAYS_ENV, ""))
    return None if days is None else timedelta(days=days)


#: How long a run waits before it outranks everything (FR-089). One hour: long
#: enough that priority means something under load, short enough that
#: "eventually" is a promise inside a business day.
DEFAULT_STARVATION = timedelta(hours=1)
STARVATION_SECONDS_ENV = "DOCDOC_RUN_STARVATION_SECONDS"


def configured_starvation(explicit: int | None = None) -> timedelta:
    """The starvation bound: explicit, then environment, then one hour."""
    if explicit is not None:
        return timedelta(seconds=_demand_positive(explicit, "--starvation-seconds"))
    seconds = _positive_int(os.environ.get(STARVATION_SECONDS_ENV, ""))
    return DEFAULT_STARVATION if seconds is None else timedelta(seconds=seconds)


#: How far back "recently served" looks when the claim breaks a tie between
#: tenants (FR-090). Ten minutes: long enough that a tenant which just had a run
#: yields to one that has had none, short enough that a tenant idle for an hour
#: is not still paying for it.
DEFAULT_FAIRNESS_WINDOW = timedelta(minutes=10)


#: -- The maintenance tick ---------------------------------------------------
#:
#: How often the tick may run, and how long one may take. Both belong to the
#: deployment rather than to an invocation: the worker has no flags for them,
#: because a process that sweeps and delivers between claims is configured by
#: whoever deployed it.
#:
#: **Spelled with their units**, which corrects `contracts/operations-layer.md`
#: and `packaging/docker/compose.yml`, both of which named `DOCDOC_MAINTENANCE_
#: INTERVAL`. This project's own rule is that a duration carries its unit in its
#: name, so that a value cannot be read as "1h" by one reader and "1" by another
#: — the same reason `DOCDOC_RUN_LEASE_SECONDS` is not `DOCDOC_RUN_LEASE`.
MAINTENANCE_INTERVAL_ENV = "DOCDOC_MAINTENANCE_INTERVAL_SECONDS"
MAINTENANCE_BUDGET_ENV = "DOCDOC_MAINTENANCE_BUDGET_MS"


def configured_maintenance_interval(explicit: int | None = None) -> int:
    """Seconds between ticks: explicit, then environment, then sixty."""
    from docdoc.runs import maintenance

    if explicit is not None:
        return _demand_positive(explicit, "--maintenance-interval-seconds")
    return (
        _positive_int(os.environ.get(MAINTENANCE_INTERVAL_ENV, ""))
        or maintenance.DEFAULT_INTERVAL_SECONDS
    )


def configured_maintenance_budget(explicit: int | None = None) -> int:
    """Milliseconds one tick may take: explicit, then environment, then 5 000."""
    from docdoc.runs import maintenance

    if explicit is not None:
        return _demand_positive(explicit, "--maintenance-budget-ms")
    return (
        _positive_int(os.environ.get(MAINTENANCE_BUDGET_ENV, "")) or maintenance.DEFAULT_BUDGET_MS
    )


#: -- Delivery (ADR-0018) ----------------------------------------------------
#:
#: Here rather than in `delivery.py` for the reason `LIMIT_WINDOWS` is here: the
#: backoff schedule is arithmetic on a `datetime`, and
#: `tests/unit/test_runs_clock_confinement.py` permits exactly one module in this
#: package to import that name. Widening it to let the deliverer add a
#: `timedelta` to an instant is the change FR-096a forbids.

#: How many times one delivery is attempted before it comes to rest at `failed`
#: (FR-057). Six attempts on the default backoff spans a little over eight hours,
#: which is long enough to cross a receiver's deployment and short enough that a
#: dead endpoint stops costing anything within a day.
DEFAULT_DELIVERY_ATTEMPTS = 6

#: How long one attempt may take, end to end. Ten seconds, because this runs
#: inside a maintenance tick between claims: the worst case a claim waits is one
#: tick budget plus one of these, and that is the number SC-024 measures.
DEFAULT_DELIVERY_TIMEOUT_SECONDS = 10

#: The first retry's delay, doubled per attempt thereafter.
DEFAULT_DELIVERY_BACKOFF_SECONDS = 30

#: How many due deliveries one maintenance tick attempts, before the sweep and
#: sharing its budget (contracts/operations-layer.md).
DEFAULT_DELIVERY_BATCH = 32

DELIVERY_ATTEMPTS_ENV = "DOCDOC_DELIVERY_MAX_ATTEMPTS"
DELIVERY_TIMEOUT_ENV = "DOCDOC_DELIVERY_TIMEOUT_SECONDS"
DELIVERY_BACKOFF_ENV = "DOCDOC_DELIVERY_BACKOFF_SECONDS"
DELIVERY_BATCH_ENV = "DOCDOC_MAINTENANCE_DELIVERY_BATCH"

#: Permits a destination the policy would otherwise refuse: a private, loopback,
#: link-local, multicast, reserved, or unspecified address, and a plain `http`
#: URL. One flag for both because they have one use between them — a receiver on
#: the same host or the same private network, which an operator running docdoc
#: and their own consumer inside one perimeter genuinely has.
#:
#: **Off by default, and it is the setting that turns the SSRF guard off.** Named
#: for what it permits rather than for the guard it disables, because an operator
#: reading a variable list should see the capability they are granting.
DELIVERY_ALLOW_PRIVATE_ENV = "DOCDOC_DELIVERY_ALLOW_PRIVATE"

#: Where the signing secrets live. A file for the reason the key file is one
#: (research R14): a secret set is a list, file permissions are a control the
#: environment does not offer, and `argv` is readable by every process on the
#: host.
DELIVERY_SECRETS_FILE_ENV = "DOCDOC_DELIVERY_SECRETS_FILE"


def configured_delivery_attempts(explicit: int | None = None) -> int:
    """The attempt limit: explicit, then environment, then six."""
    if explicit is not None:
        return _demand_positive(explicit, "--delivery-attempts")
    return _positive_int(os.environ.get(DELIVERY_ATTEMPTS_ENV, "")) or DEFAULT_DELIVERY_ATTEMPTS


def configured_delivery_timeout(explicit: int | None = None) -> int:
    """How long one attempt may take: explicit, then environment, then ten seconds."""
    if explicit is not None:
        return _demand_positive(explicit, "--delivery-timeout-seconds")
    return (
        _positive_int(os.environ.get(DELIVERY_TIMEOUT_ENV, "")) or DEFAULT_DELIVERY_TIMEOUT_SECONDS
    )


def configured_delivery_backoff(explicit: int | None = None) -> int:
    """The first retry's delay in seconds: explicit, then environment, then thirty."""
    if explicit is not None:
        return _demand_positive(explicit, "--delivery-backoff-seconds")
    return (
        _positive_int(os.environ.get(DELIVERY_BACKOFF_ENV, "")) or DEFAULT_DELIVERY_BACKOFF_SECONDS
    )


def configured_delivery_batch(explicit: int | None = None) -> int:
    """How many due deliveries one tick attempts. Same precedence."""
    if explicit is not None:
        return _demand_positive(explicit, "--delivery-batch")
    return _positive_int(os.environ.get(DELIVERY_BATCH_ENV, "")) or DEFAULT_DELIVERY_BATCH


def delivery_allows_private() -> bool:
    """Whether the destination policy has been explicitly relaxed (FR-060).

    Any of the usual affirmatives, and **absent means no**. A guard against
    server-side request forgery that could be switched off by a malformed value
    would be a guard that a typo disables.
    """
    return os.environ.get(DELIVERY_ALLOW_PRIVATE_ENV, "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def backoff(at: datetime, *, attempts: int, base_seconds: int) -> datetime:
    """When an attempt that has just failed should be tried again.

    Exponential, doubling per attempt, and capped at a day. The cap is not
    arithmetic tidiness: without one the sixth retry of a configurable base can
    land beyond any retention period, so a delivery would be scheduled for an
    instant after the run it describes has been swept.

    Takes `at` rather than reading a clock, which is what lets a whole retry
    schedule be asserted in a unit test at an arbitrary instant.
    """
    delay = base_seconds * (2 ** max(0, attempts - 1))
    return at + timedelta(seconds=min(delay, _BACKOFF_CAP_SECONDS))


#: One day. Long enough that six attempts on any sensible base fit inside it,
#: short enough that a retry cannot outlive the run row it describes.
_BACKOFF_CAP_SECONDS = 24 * 60 * 60


#: The window each counted limit falls into (research R9).
#:
#: Here rather than in `limits.py` for the reason everything else in this module
#: is here: `tests/unit/test_runs_clock_confinement.py` permits exactly one
#: module in this package to import `datetime`, and a table of `timedelta`s is an
#: import of it. The guard cannot tell a duration from a clock read, and widening
#: it to teach it the difference is what FR-096a forbids -- so the durations live
#: with the clock instead.
LIMIT_WINDOWS = {
    "submissions": timedelta(minutes=1),
    "runs_per_period": timedelta(days=30),
    "tokens": timedelta(days=30),
}

#: How long a counter row is kept before the maintenance tick removes it.
#:
#: Twice the longest window, and the doubling is what makes it safe rather than
#: tight: a check reads the window `now` falls in, so removing rows at exactly
#: one window would race the check reading the one that just ended. At twice it
#: cannot, and the cost of keeping a spent counter one extra period is one row
#: per tenant per kind.
#:
#: Here rather than in `limits.py` for the reason `LIMIT_WINDOWS` is here: it is
#: a `timedelta`, and this is the only module in the package permitted to import
#: the name (FR-096a).
LIMIT_RETENTION = max(LIMIT_WINDOWS.values()) * 2
