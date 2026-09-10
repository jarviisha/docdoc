"""An in-memory limiter, so window policy is testable with no database.

Here for the reason `InMemoryRunQueue` is here: what is worth testing about
limits is the **policy** — when a window rolls, what the boundary burst costs,
whether concurrency is counted rather than accumulated — and none of that needs
Postgres. A test that did would skip on every machine without one.

It shares `PostgresLimiter`'s window arithmetic by calling the same
`identity.window_start`, which is what keeps the two from drifting into two
different definitions of "this minute".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from docdoc.runs.identity import LIMIT_WINDOWS as _WINDOWS
from docdoc.runs.identity import window_start
from docdoc.runs.limits import (
    KIND_RUNS_PER_PERIOD,
    KIND_SUBMISSIONS,
    KIND_TOKENS,
    LimitPolicy,
    LimitVerdict,
)

if TYPE_CHECKING:
    from datetime import datetime

__all__ = ["InMemoryLimiter"]


@dataclass
class InMemoryLimiter:
    """The same four checks over a dictionary."""

    policy: LimitPolicy = field(default_factory=LimitPolicy)
    #: `{(tenant, kind, window_start): value}`, exactly the table's primary key.
    counters: dict[tuple[str, str, datetime], int] = field(default_factory=dict)
    #: What `_concurrency_check` would read from `runs`. Set by the test.
    active: dict[str, int] = field(default_factory=dict)

    def check(self, *, tenant_id: str, now: datetime) -> LimitVerdict:
        if not self.policy.configured:
            return LimitVerdict()

        for kind, permitted in (
            (KIND_SUBMISSIONS, self.policy.submissions_per_minute),
            (KIND_RUNS_PER_PERIOD, self.policy.runs_per_period),
            (KIND_TOKENS, self.policy.tokens_per_period),
        ):
            if permitted is None:
                continue
            window = _WINDOWS[kind]
            start = window_start(now, window)
            observed = self.counters.get((tenant_id, kind, start), 0)
            if observed >= permitted:
                return LimitVerdict(
                    allowed=False,
                    limit=kind,
                    observed=observed,
                    permitted=permitted,
                    retry_after_seconds=int((start + window - now).total_seconds()) + 1,
                )

        permitted = self.policy.concurrent_runs
        if permitted is not None:
            observed = self.active.get(tenant_id, 0)
            if observed >= permitted:
                return LimitVerdict(
                    allowed=False,
                    limit="concurrent_runs",
                    observed=observed,
                    permitted=permitted,
                    retry_after_seconds=30,
                )

        return LimitVerdict()

    def record_submission(self, *, tenant_id: str, now: datetime) -> None:
        for kind, permitted in (
            (KIND_SUBMISSIONS, self.policy.submissions_per_minute),
            (KIND_RUNS_PER_PERIOD, self.policy.runs_per_period),
        ):
            if permitted is not None:
                self._add(tenant_id, kind, 1, now)

    def record_tokens(self, *, tenant_id: str, tokens: int, now: datetime) -> None:
        if self.policy.tokens_per_period is not None and tokens > 0:
            self._add(tenant_id, KIND_TOKENS, tokens, now)

    def _add(self, tenant_id: str, kind: str, by: int, now: datetime) -> None:
        key = (tenant_id, kind, window_start(now, _WINDOWS[kind]))
        self.counters[key] = self.counters.get(key, 0) + by
