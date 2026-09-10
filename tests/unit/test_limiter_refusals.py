"""`PostgresLimiter` — which limit refused, and what a caller is told to do.

The postgres integration tests exercise counting against a live table. What was
never checked is the shape of the *refusal*: which limit it names, what
`retry_after` it carries, and that a tenant at exactly the permitted number is
still allowed. Those are the parts a client's retry loop reads, and an off-by-one
here refuses a customer who is inside their cap.

`execute` is the queue's statement runner, passed in rather than reached for — so
a stub answers every query and none of this needs a database. That is the same
seam the class was designed around, not a testing workaround.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from docdoc.runs.errors import LimitExceededError
from docdoc.runs.limits import LimitBook, LimitPolicy, PostgresLimiter

#: Deliberately mid-window: a `retry_after` computed from a window boundary is
#: only interesting when `now` is not on one.
NOW = datetime(2026, 9, 10, 12, 0, 20, tzinfo=UTC)


class _Table:
    """Answers the two reads the limiter makes and records every write."""

    def __init__(self, *, counter: int | None = None, active: int | None = None):
        self._counter = counter
        self._active = active
        self.statements: list[tuple[str, tuple]] = []

    def __call__(self, sql: str, params: tuple = (), *, fetch: str | None = None) -> Any:
        self.statements.append((sql, params))
        if fetch != "one":
            return [] if fetch == "all" else None
        if "limit_counters" in sql:
            return None if self._counter is None else {"value": self._counter}
        return None if self._active is None else {"active": self._active}


def _limiter(policy: Any, **table: Any) -> tuple[PostgresLimiter, _Table]:
    execute = _Table(**table)
    return PostgresLimiter(execute=execute, policy=policy), execute


# -- nothing configured ------------------------------------------------------


def test_an_unconfigured_policy_asks_the_database_nothing() -> None:
    """FR-049. A deployment that configured no limits pays for none."""
    limiter, table = _limiter(LimitPolicy())

    assert limiter.check(tenant_id="acme", now=NOW).allowed is True
    assert table.statements == []


# -- the boundary ------------------------------------------------------------


@pytest.mark.parametrize(("observed", "allowed"), [(58, True), (59, True), (60, False)])
def test_the_permitted_number_is_permitted(observed: int, allowed: bool) -> None:
    """`observed < permitted`, so a tenant whose 60th submission this is gets
    it. The other reading refuses a customer at exactly their cap."""
    limiter, _ = _limiter(LimitPolicy(submissions_per_minute=60), counter=observed)

    assert limiter.check(tenant_id="acme", now=NOW).allowed is allowed


def test_no_counter_row_yet_is_zero_and_not_a_refusal() -> None:
    limiter, _ = _limiter(LimitPolicy(submissions_per_minute=1), counter=None)

    assert limiter.check(tenant_id="acme", now=NOW).allowed is True


# -- what the refusal says ---------------------------------------------------


def test_a_refusal_names_the_limit_and_when_to_come_back() -> None:
    """`retry_after` is the rest of the window, plus one.

    Plus one because a client that retried at the exact boundary would race the
    window rollover and be refused a second time for no reason it could see.
    """
    limiter, _ = _limiter(LimitPolicy(submissions_per_minute=60), counter=60)

    verdict = limiter.check(tenant_id="acme", now=NOW)

    assert verdict.allowed is False
    assert verdict.limit == "submissions"
    assert verdict.observed == 60
    assert verdict.permitted == 60
    # 20 seconds into a one-minute window: 40 left, plus one.
    assert verdict.retry_after_seconds == 41


def test_a_concurrency_refusal_gives_a_hint_and_not_a_schedule() -> None:
    """What frees this is a run finishing, and docdoc cannot say when.

    A long `retry_after` here would invent a schedule; a short one is honest.
    """
    limiter, _ = _limiter(LimitPolicy(concurrent_runs=4), active=4)

    verdict = limiter.check(tenant_id="acme", now=NOW)

    assert verdict.limit == "concurrent_runs"
    assert verdict.retry_after_seconds == 30


def test_concurrency_is_counted_from_runs_and_never_stored() -> None:
    """A stored count would drift the first time a worker died holding one."""
    limiter, table = _limiter(LimitPolicy(concurrent_runs=1), active=0)

    limiter.check(tenant_id="acme", now=NOW)

    assert any("FROM runs" in sql for sql, _ in table.statements)
    assert not any("limit_counters" in sql for sql, _ in table.statements)


def test_a_verdict_raises_the_error_the_route_returns() -> None:
    limiter, _ = _limiter(LimitPolicy(concurrent_runs=1), active=9)

    with pytest.raises(LimitExceededError):
        limiter.check(tenant_id="acme", now=NOW).raise_if_refused()


# -- which limit is reported -------------------------------------------------


def test_the_first_limit_reached_is_the_one_named() -> None:
    """Order matters only for cost. A tenant over two limits is over two limits
    either way, and naming one is what a client can act on."""
    policy = LimitPolicy(submissions_per_minute=1, runs_per_period=1, concurrent_runs=1)
    limiter, _ = _limiter(policy, counter=99, active=99)

    assert limiter.check(tenant_id="acme", now=NOW).limit == "submissions"


def test_a_tenant_override_is_what_gets_enforced() -> None:
    """FR-050. The per-tenant number, not the deployment's."""
    book = LimitBook(
        default=LimitPolicy(submissions_per_minute=1),
        by_tenant={"acme": LimitPolicy(submissions_per_minute=100)},
    )
    limiter, _ = _limiter(book, counter=50)

    assert limiter.check(tenant_id="acme", now=NOW).allowed is True
    assert limiter.check(tenant_id="globex", now=NOW).allowed is False


# -- counting ----------------------------------------------------------------


def test_a_submission_increments_only_the_windows_that_exist() -> None:
    """A counter row for a limit nobody configured is storage that refuses
    nothing — the accumulation these limits exist to prevent, by the back door."""
    limiter, table = _limiter(LimitPolicy(submissions_per_minute=60))

    limiter.record_submission(tenant_id="acme", now=NOW)

    kinds = [params[1] for sql, params in table.statements if "INSERT" in sql]
    assert kinds == ["submissions"]


def test_both_run_windows_are_incremented_when_both_are_configured() -> None:
    limiter, table = _limiter(LimitPolicy(submissions_per_minute=60, runs_per_period=1000))

    limiter.record_submission(tenant_id="acme", now=NOW)

    kinds = [params[1] for sql, params in table.statements if "INSERT" in sql]
    assert kinds == ["submissions", "runs_per_period"]


@pytest.mark.parametrize("tokens", [0, -1])
def test_a_run_that_consumed_nothing_writes_no_row(tokens: int) -> None:
    limiter, table = _limiter(LimitPolicy(tokens_per_period=1000))

    limiter.record_tokens(tenant_id="acme", tokens=tokens, now=NOW)

    assert table.statements == []


def test_tokens_are_not_counted_when_no_budget_is_configured() -> None:
    limiter, table = _limiter(LimitPolicy(submissions_per_minute=60))

    limiter.record_tokens(tenant_id="acme", tokens=5000, now=NOW)

    assert table.statements == []


def test_expired_windows_are_removed_and_counted() -> None:
    """T175b. A counter table that only grows is the failure this feature exists
    to prevent, and it would be an embarrassing one."""
    execute = _Table()
    execute_all = lambda sql, params=(), *, fetch=None: (  # noqa: E731
        execute(sql, params, fetch=fetch) or [{"kind": "submissions"}, {"kind": "tokens"}]
    )
    limiter = PostgresLimiter(execute=execute_all, policy=LimitPolicy())

    assert limiter.expire_windows(now=NOW, keep=timedelta(hours=2)) == 2
    assert "DELETE FROM limit_counters" in execute.statements[0][0]
