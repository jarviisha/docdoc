"""Per-tenant limits, counted in order to refuse and never in order to bill.

Constitution **v1.8.0** draws that line explicitly, and it had to be drawn by
amendment rather than by argument: a per-tenant token counter is metering under
the plainest reading of the sentence it amends, and a spec that reinterpreted
that sentence to comply with it would invert the precedence Governance sets.

So: nothing in this module prices, invoices, or emits a billing record. A counter
that exists to say "not now" is a limit; the same counter exported to an
accounts-receivable system is metering, and adding the second needs its own
amendment.

**Four limits, three mechanisms, and the differences are the design.**

*Submissions per interval* and *runs per period* are fixed-window counters, one
row per tenant per kind per window, incremented by a single statement.

*Concurrency* is **counted from `runs`**, not stored. The table already knows how
many of a tenant's runs are not terminal; a counter beside it is a second copy of
a fact that can drift, and drift here means either refusing valid work forever or
never refusing anything.

*The token budget* is different in kind. Tokens are known only after a provider
answers, so this refuses the **next** submission and can never abort the one in
flight (FR-047). Enforcing mid-run would discard work already paid for, which is
the exact failure Milestone 9 existed to remove.

**Checked at submission and nowhere else** (FR-045), and there is no
interposition point that could do otherwise.

**Nothing here reads a clock.** ``now`` is a parameter and windows are derived by
`identity.window_start`, because that module is the only one in this package
permitted to reach an ambient source (FR-096a).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from docdoc.runs.errors import LimitExceededError
from docdoc.runs.identity import LIMIT_WINDOWS as _WINDOWS

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime, timedelta

__all__ = [
    "KIND_RUNS_PER_PERIOD",
    "KIND_SUBMISSIONS",
    "KIND_TOKENS",
    "LimitBook",
    "LimitPolicy",
    "LimitVerdict",
    "Limiter",
    "NullLimiter",
    "PostgresLimiter",
]

_logger = logging.getLogger("docdoc.runs")

#: The three counted kinds. Concurrency is deliberately absent: it is read from
#: `runs` rather than accumulated (see the module docstring).
KIND_SUBMISSIONS = "submissions"
KIND_RUNS_PER_PERIOD = "runs_per_period"
KIND_TOKENS = "tokens"


@dataclass(frozen=True)
class LimitBook:
    """Per-tenant limits, with a deployment-wide default (FR-050).

    Loaded from a JSON file, on the same reasoning that makes the key ring a file
    (research R14): a per-tenant table of anything is a list, and file
    permissions are a control the environment does not offer. It also keeps the
    four limits readable in one place rather than as four variables times the
    number of tenants.

    ```json
    {"default": {"submissions_per_minute": 60},
     "tenants": {"bulk": {"submissions_per_minute": 600, "concurrent_runs": 20}}}
    ```

    A tenant with no entry gets the default; a tenant whose entry omits a field
    gets the default's value for that field. **Absent is not zero** — it means
    the limit does not exist for that tenant.
    """

    default: LimitPolicy
    by_tenant: dict[str, LimitPolicy]

    def policy_for(self, tenant_id: str) -> LimitPolicy:
        override = self.by_tenant.get(tenant_id)
        if override is None:
            return self.default
        return LimitPolicy(
            submissions_per_minute=(
                override.submissions_per_minute
                if override.submissions_per_minute is not None
                else self.default.submissions_per_minute
            ),
            concurrent_runs=(
                override.concurrent_runs
                if override.concurrent_runs is not None
                else self.default.concurrent_runs
            ),
            runs_per_period=(
                override.runs_per_period
                if override.runs_per_period is not None
                else self.default.runs_per_period
            ),
            tokens_per_period=(
                override.tokens_per_period
                if override.tokens_per_period is not None
                else self.default.tokens_per_period
            ),
        )

    @property
    def configured(self) -> bool:
        return self.default.configured or any(
            policy.configured for policy in self.by_tenant.values()
        )

    @classmethod
    def from_file(cls, path: Any, default: LimitPolicy) -> LimitBook:
        """Read the overrides, refusing anything it cannot make sense of.

        Strict for the same reason `KeyRing.from_file` is: every failure here is
        a deployment that thinks it has limits and does not, and the quiet
        version — skipping an entry and starting anyway — serves traffic while
        one customer's cap is silently absent.
        """
        import json

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except OSError as error:
            raise ValueError(f"{path} cannot be read: {type(error).__name__}") from error
        except json.JSONDecodeError as error:
            raise ValueError(f"{path} is not valid JSON") from error

        if not isinstance(raw, dict):
            raise ValueError(f"{path} must be an object")

        book_default = _policy_from(raw.get("default") or {}, path, "default")
        tenants = raw.get("tenants") or {}
        if not isinstance(tenants, dict):
            raise ValueError(f"{path}: `tenants` must be an object")

        return cls(
            default=book_default if book_default.configured else default,
            by_tenant={
                tenant_id: _policy_from(entry, path, tenant_id)
                for tenant_id, entry in tenants.items()
            },
        )


def _policy_from(entry: Any, path: Any, where: str) -> LimitPolicy:
    if not isinstance(entry, dict):
        raise ValueError(f"{path}: entry for {where!r} is not an object")
    counted = {
        "submissions_per_minute",
        "concurrent_runs",
        "runs_per_period",
        "tokens_per_period",
    }
    # A ceiling is a per-tenant policy in the same file and is deliberately **not**
    # a `LimitPolicy` field: nothing counts it, nothing refuses on it, and giving
    # this model a fifth attribute that the limiter never reads would be the
    # second source of truth this module has otherwise avoided. It is read by the
    # HTTP layer, which is the only thing that grants a priority; accepted here so
    # a file carrying one is not rejected as a typo (FR-087b).
    unknown = set(entry) - counted - {"priority_ceiling"}
    if unknown:
        # A misspelled limit is a limit that is not applied, and silence about it
        # is how a deployment believes it is protected and is not.
        raise ValueError(f"{path}: entry for {where!r} names unknown limits {sorted(unknown)}")
    return LimitPolicy(**{name: int(value) for name, value in entry.items() if name in counted})


@dataclass(frozen=True)
class LimitPolicy:
    """What a deployment configured. ``None`` everywhere means count nothing.

    Every field defaults to `None`, and `None` is not "zero" — it is "this limit
    does not exist here". A deployment that configures none counts nothing,
    refuses nothing, and behaves exactly as Milestone 9 did (FR-049).
    """

    submissions_per_minute: int | None = None
    concurrent_runs: int | None = None
    runs_per_period: int | None = None
    tokens_per_period: int | None = None

    @property
    def configured(self) -> bool:
        return any(
            value is not None
            for value in (
                self.submissions_per_minute,
                self.concurrent_runs,
                self.runs_per_period,
                self.tokens_per_period,
            )
        )


@dataclass(frozen=True)
class LimitVerdict:
    """Allowed, or a refusal naming what a client needs to act.

    Four fields, because being over a quota is **not a secret**. An
    authentication refusal says nothing at all — deliberately — and a client told
    only "no" retries immediately and forever. These two refusals must not be
    confusable, which is why one is a 429 naming the limit and the other is a 401
    naming nothing (FR-043).
    """

    allowed: bool = True
    limit: str | None = None
    observed: int = 0
    permitted: int = 0
    retry_after_seconds: int = 0

    def raise_if_refused(self) -> None:
        if self.allowed:
            return
        raise LimitExceededError(
            self.limit or "unknown",
            observed=self.observed,
            allowed=self.permitted,
            retry_after_seconds=self.retry_after_seconds,
        )


class Limiter(Protocol):
    """What the submission route asks before it creates anything."""

    def check(self, *, tenant_id: str, now: datetime) -> LimitVerdict:
        """Whether this tenant may submit now."""
        ...

    def record_submission(self, *, tenant_id: str, now: datetime) -> None:
        """Count an accepted submission. Called only after `check` allowed it."""
        ...

    def record_tokens(self, *, tenant_id: str, tokens: int, now: datetime) -> None:
        """Count what a completed run consumed (FR-042).

        Called by the worker in the transaction that records the terminal state,
        which is what makes the budget refuse the *next* submission rather than
        this one.
        """
        ...


@dataclass(frozen=True)
class NullLimiter:
    """Allows everything, and is the default (FR-049).

    Not a test double: it is the configuration docdoc runs in when nobody asked
    for limits, and it is what makes "a deployment that configures none counts
    nothing" true by construction rather than by a flag checked correctly at
    every call site.
    """

    def check(self, *, tenant_id: str, now: datetime) -> LimitVerdict:
        return LimitVerdict()

    def record_submission(self, *, tenant_id: str, now: datetime) -> None:
        return None

    def record_tokens(self, *, tenant_id: str, tokens: int, now: datetime) -> None:
        return None


@dataclass
class PostgresLimiter:
    """Fixed windows in a table, and one count read from `runs`.

    ``execute`` is the queue's statement runner, passed in rather than reached
    for — this module holds no connection and opens none.
    """

    execute: Callable[..., Any]
    #: A single policy applied to every tenant, or a `LimitBook` carrying
    #: per-tenant overrides (FR-050). Both answer `policy_for`.
    policy: LimitPolicy | LimitBook = field(default_factory=LimitPolicy)

    def _for(self, tenant_id: str) -> LimitPolicy:
        book = self.policy
        return book.policy_for(tenant_id) if isinstance(book, LimitBook) else book

    def check(self, *, tenant_id: str, now: datetime) -> LimitVerdict:
        """The four checks, cheapest first.

        Order matters only for cost: a refusal names whichever limit was reached
        first, and a tenant over two limits is over two limits either way.
        """
        policy = self._for(tenant_id)
        if not policy.configured:
            return LimitVerdict()

        if policy.submissions_per_minute is not None:
            verdict = self._window_check(
                tenant_id,
                KIND_SUBMISSIONS,
                policy.submissions_per_minute,
                now,
            )
            if not verdict.allowed:
                return verdict

        if policy.runs_per_period is not None:
            verdict = self._window_check(
                tenant_id, KIND_RUNS_PER_PERIOD, policy.runs_per_period, now
            )
            if not verdict.allowed:
                return verdict

        if policy.tokens_per_period is not None:
            verdict = self._window_check(tenant_id, KIND_TOKENS, policy.tokens_per_period, now)
            if not verdict.allowed:
                return verdict

        if policy.concurrent_runs is not None:
            return self._concurrency_check(tenant_id, policy.concurrent_runs, now)

        return LimitVerdict()

    def record_submission(self, *, tenant_id: str, now: datetime) -> None:
        policy = self._for(tenant_id)
        if policy.submissions_per_minute is not None:
            self._increment(tenant_id, KIND_SUBMISSIONS, 1, now)
        if policy.runs_per_period is not None:
            self._increment(tenant_id, KIND_RUNS_PER_PERIOD, 1, now)

    def record_tokens(self, *, tenant_id: str, tokens: int, now: datetime) -> None:
        if self._for(tenant_id).tokens_per_period is None or tokens <= 0:
            return
        self._increment(tenant_id, KIND_TOKENS, tokens, now)

    # -- the two mechanisms ---------------------------------------------------

    def _window_check(
        self, tenant_id: str, kind: str, permitted: int, now: datetime
    ) -> LimitVerdict:
        from docdoc.runs.identity import window_start

        window = _WINDOWS[kind]
        start = window_start(now, window)
        row = self.execute(
            "SELECT value FROM limit_counters "
            "WHERE tenant_id = %s AND kind = %s AND window_start = %s",
            (tenant_id, kind, start),
            fetch="one",
        )
        observed = int(row["value"]) if row else 0
        if observed < permitted:
            return LimitVerdict()

        return _refused(
            kind,
            observed=observed,
            permitted=permitted,
            retry_after_seconds=int((start + window - now).total_seconds()) + 1,
            tenant_id=tenant_id,
        )

    def _concurrency_check(self, tenant_id: str, permitted: int, now: datetime) -> LimitVerdict:
        """Counted from `runs`, never stored (module docstring)."""
        del now
        row = self.execute(
            "SELECT count(*) AS active FROM runs "
            "WHERE tenant_id = %s AND status IN ('queued', 'running')",
            (tenant_id,),
            fetch="one",
        )
        observed = int(row["active"]) if row else 0
        if observed < permitted:
            return LimitVerdict()

        return _refused(
            "concurrent_runs",
            observed=observed,
            permitted=permitted,
            # No window to wait for: what frees this is a run finishing, and
            # docdoc cannot say when. A short hint is honest; a long one would
            # invent a schedule.
            retry_after_seconds=30,
            tenant_id=tenant_id,
        )

    def _increment(self, tenant_id: str, kind: str, by: int, now: datetime) -> None:
        """One statement (research R9). Concurrent submissions cannot lose one."""
        from docdoc.runs.identity import window_start

        self.execute(
            "INSERT INTO limit_counters (tenant_id, kind, window_start, value) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (tenant_id, kind, window_start) "
            "DO UPDATE SET value = limit_counters.value + EXCLUDED.value",
            (tenant_id, kind, window_start(now, _WINDOWS[kind]), by),
        )

    def expire_windows(self, *, now: datetime, keep: timedelta) -> int:
        """Remove counter rows older than `keep`. Called by the maintenance tick.

        A counter table that only grows is the failure this milestone exists to
        prevent, and it would be an embarrassing one.
        """
        rows = self.execute(
            "DELETE FROM limit_counters WHERE window_start < %s RETURNING kind",
            (now - keep,),
            fetch="all",
        )
        return len(rows or ())


def _refused(
    limit: str, *, observed: int, permitted: int, retry_after_seconds: int, tenant_id: str
) -> LimitVerdict:
    """Build the refusal and emit the event (FR-052)."""
    _logger.info(
        "limit.refused",
        extra={
            "docdoc": {
                "event": "limit.refused",
                "tenant_id": tenant_id,
                "limit": limit,
                "observed": observed,
                "allowed": permitted,
            }
        },
    )
    return LimitVerdict(
        allowed=False,
        limit=limit,
        observed=observed,
        permitted=permitted,
        retry_after_seconds=retry_after_seconds,
    )
