"""T098, T099, T103 — the four limits, and the cost the fixed window carries.

**The boundary burst is asserted rather than avoided.** A fixed window lets a
tenant issue up to twice its limit across a boundary, and that is documented in
`docs/concepts/limits.md` rather than engineered away. Research R9 gives the
trade: a sliding window needs either a row per submission — making the limiter
the largest table in this database — or a background decay process, which is the
fifth process FR-116 forbids. What actually protects a deployment is the worker
pool size, and that is unaffected by the burst.

A test that let the burst pass silently would be a test agreeing with an
implementation. This one names it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from docdoc.runs.errors import LimitExceededError
from docdoc.runs.limits import KIND_SUBMISSIONS, LimitPolicy, NullLimiter
from tests.fixtures.limiter import InMemoryLimiter

NOW = datetime(2026, 9, 4, 12, 0, 30, tzinfo=UTC)


class TestNothingIsCountedUntilAsked:
    """FR-049 — a deployment configuring none behaves as Milestone 9 did."""

    def test_the_null_limiter_allows_everything(self) -> None:
        limiter = NullLimiter()

        for _ in range(1000):
            assert limiter.check(tenant_id="acme", now=NOW).allowed

    def test_an_unconfigured_policy_counts_nothing(self) -> None:
        limiter = InMemoryLimiter(policy=LimitPolicy())

        for _ in range(100):
            limiter.record_submission(tenant_id="acme", now=NOW)

        assert limiter.counters == {}
        assert limiter.check(tenant_id="acme", now=NOW).allowed


class TestSubmissionsPerInterval:
    def test_it_refuses_at_the_limit_and_names_it(self) -> None:
        limiter = InMemoryLimiter(policy=LimitPolicy(submissions_per_minute=2))

        for _ in range(2):
            assert limiter.check(tenant_id="acme", now=NOW).allowed
            limiter.record_submission(tenant_id="acme", now=NOW)

        verdict = limiter.check(tenant_id="acme", now=NOW)

        assert verdict.allowed is False
        assert verdict.limit == KIND_SUBMISSIONS
        assert (verdict.observed, verdict.permitted) == (2, 2)
        assert verdict.retry_after_seconds > 0

    def test_one_tenant_does_not_spend_anothers_allowance(self) -> None:
        limiter = InMemoryLimiter(policy=LimitPolicy(submissions_per_minute=1))
        limiter.record_submission(tenant_id="bulk", now=NOW)

        assert limiter.check(tenant_id="acme", now=NOW).allowed
        assert limiter.check(tenant_id="bulk", now=NOW).allowed is False

    def test_the_window_rolls(self) -> None:
        limiter = InMemoryLimiter(policy=LimitPolicy(submissions_per_minute=1))
        limiter.record_submission(tenant_id="acme", now=NOW)

        assert limiter.check(tenant_id="acme", now=NOW).allowed is False
        assert limiter.check(tenant_id="acme", now=NOW + timedelta(minutes=1)).allowed


class TestTheBoundaryBurstIsReal:
    """R9's stated cost, asserted so nobody discovers it in production."""

    def test_a_tenant_can_issue_twice_its_limit_across_a_boundary(self) -> None:
        limiter = InMemoryLimiter(policy=LimitPolicy(submissions_per_minute=3))
        late = datetime(2026, 9, 4, 12, 0, 59, tzinfo=UTC)
        early = datetime(2026, 9, 4, 12, 1, 0, tzinfo=UTC)

        issued = 0
        for at in (late, late, late, early, early, early):
            if limiter.check(tenant_id="acme", now=at).allowed:
                limiter.record_submission(tenant_id="acme", now=at)
                issued += 1

        assert issued == 6, (
            "the fixed window did not permit the documented burst. If this is now "
            "3, the mechanism changed to a sliding window -- which needs a row "
            "per submission or a decay process, and docs/concepts/limits.md "
            "promises neither."
        )


class TestConcurrencyIsCountedNotAccumulated:
    """FR-040 — the runs table already knows, and a counter would drift."""

    def test_it_reads_what_is_in_flight(self) -> None:
        limiter = InMemoryLimiter(policy=LimitPolicy(concurrent_runs=2))
        limiter.active["acme"] = 2

        assert limiter.check(tenant_id="acme", now=NOW).allowed is False

        limiter.active["acme"] = 1
        assert limiter.check(tenant_id="acme", now=NOW).allowed

    def test_it_is_not_spent_by_submissions(self) -> None:
        """A submission that finished frees the slot without anybody decrementing."""
        limiter = InMemoryLimiter(policy=LimitPolicy(concurrent_runs=1))
        limiter.record_submission(tenant_id="acme", now=NOW)
        limiter.record_submission(tenant_id="acme", now=NOW)

        assert limiter.check(tenant_id="acme", now=NOW).allowed


class TestTheTokenBudgetIsOneSubmissionLate:
    """FR-047 — and that is the safe direction."""

    def test_the_run_that_exceeds_it_is_not_refused(self) -> None:
        limiter = InMemoryLimiter(policy=LimitPolicy(tokens_per_period=100))

        assert limiter.check(tenant_id="acme", now=NOW).allowed
        # The run executes, and only then is its cost known.
        limiter.record_tokens(tenant_id="acme", tokens=250, now=NOW)

        assert limiter.check(tenant_id="acme", now=NOW).allowed is False

    def test_nothing_can_refuse_a_run_already_executing(self) -> None:
        """There is no verb here that could, and that is the assertion.

        A budget that could abort in flight would reintroduce the
        paid-and-discarded failure Milestone 9 was built to remove, so the
        protocol offers no way to express it.
        """
        limiter = InMemoryLimiter(policy=LimitPolicy(tokens_per_period=1))

        assert not hasattr(limiter, "abort")
        assert not hasattr(limiter, "cancel")


class TestARefusalSaysWhatAClientNeeds:
    """FR-043 — being over a quota is not a secret."""

    def test_the_error_carries_four_facts(self) -> None:
        verdict = InMemoryLimiter(policy=LimitPolicy(submissions_per_minute=0)).check(
            tenant_id="acme", now=NOW
        )

        with pytest.raises(LimitExceededError) as caught:
            verdict.raise_if_refused()

        assert caught.value.limit == KIND_SUBMISSIONS
        assert caught.value.allowed == 0
        assert caught.value.retry_after_seconds > 0

    def test_an_allowed_verdict_raises_nothing(self) -> None:
        InMemoryLimiter().check(tenant_id="acme", now=NOW).raise_if_refused()
