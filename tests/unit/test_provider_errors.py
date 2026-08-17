"""T049, T050 — retry classification and the two bounds (FR-025, FR-026, SC-017, R12).

The retry policy lives in one place for every adapter, so this tests the policy
rather than a provider. ``sleep`` is injected: a retry test that actually waits is
a slow test, and a slow test gets marked skip.

The case worth reading is the deadline one. A service that asks for a wait longer
than the remaining budget must fail on the deadline rather than sleep past it —
honouring the request would let one `Retry-After` silently extend an extraction
past the bound the caller set.
"""

from __future__ import annotations

from typing import Any

import pytest

from docdoc.extraction import ModelProviderError, ModelResponse, ModelUsage
from docdoc.extraction.retry import call_with_retries
from docdoc.ingest import TransportSettings

TRANSIENT = ("timeout", "rate_limit", "transport", "service")
PERMANENT = ("auth", "refusal", "request", "unavailable", "deadline")


def _ok() -> ModelResponse:
    return ModelResponse(payload={}, model_id="m", model_version="1", usage=ModelUsage())


class _Recorder:
    """Counts sleeps and remembers how long each would have been."""

    def __init__(self) -> None:
        self.waits: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.waits.append(seconds)


def _failing(reason: str, *, times: int = 99, retry_after: float | None = None) -> Any:
    calls = {"n": 0}

    def call() -> ModelResponse:
        calls["n"] += 1
        if calls["n"] > times:
            return _ok()
        raise ModelProviderError(
            f"simulated {reason}",
            reason=reason,
            adapter_id="probe",
            retry_after_s=retry_after,
        )

    call.calls = calls  # type: ignore[attr-defined]
    return call


def _run(call: Any, transport: TransportSettings | None = None, sleep: Any = None):
    return call_with_retries(
        call,
        transport=transport or TransportSettings(),
        adapter_id="probe",
        document_id="sha256:doc",
        schema_identity="invoice@1",
        sleep=sleep or _Recorder(),
    )


# -- classification ----------------------------------------------------------


@pytest.mark.parametrize("reason", TRANSIENT)
def test_transient_failures_are_retried_to_the_limit(reason: str) -> None:
    call = _failing(reason)
    with pytest.raises(ModelProviderError) as caught:
        _run(call, TransportSettings(max_attempts=3))
    assert call.calls["n"] == 3
    assert caught.value.attempts == 3


@pytest.mark.parametrize("reason", PERMANENT)
def test_permanent_failures_are_not_retried(reason: str) -> None:
    """FR-025 — re-sending gets the same answer and only spends the deadline."""
    call = _failing(reason)
    with pytest.raises(ModelProviderError) as caught:
        _run(call, TransportSettings(max_attempts=5))
    assert call.calls["n"] == 1, f"{reason} must fail on the first attempt"
    assert caught.value.attempts == 1


def test_a_refusal_is_permanent_even_though_it_is_not_a_transport_failure() -> None:
    """R12 — the one that arrives as a *successful* response.

    Retrying a refusal re-sends the same content and gets the same decision. The
    only thing it buys is the attempt budget.
    """
    call = _failing("refusal")
    with pytest.raises(ModelProviderError) as caught:
        _run(call, TransportSettings(max_attempts=5))
    assert call.calls["n"] == 1
    assert caught.value.transient is False


def test_a_transient_failure_that_recovers_returns_the_response() -> None:
    call = _failing("service", times=2)
    response, attempts = _run(call, TransportSettings(max_attempts=5))
    assert isinstance(response, ModelResponse)
    assert attempts == 3


def test_a_first_attempt_success_makes_no_sleep() -> None:
    recorder = _Recorder()
    _, attempts = _run(lambda: _ok(), sleep=recorder)
    assert attempts == 1
    assert recorder.waits == []


# -- backoff -----------------------------------------------------------------


def test_backoff_grows_and_is_bounded() -> None:
    recorder = _Recorder()
    transport = TransportSettings(max_attempts=4, initial_backoff_s=1.0, max_backoff_s=4.0)
    with pytest.raises(ModelProviderError):
        _run(_failing("service"), transport, recorder)
    assert len(recorder.waits) == 3, "one wait between each pair of attempts, none after the last"
    assert recorder.waits[0] <= recorder.waits[-1]
    assert all(w <= 4.0 for w in recorder.waits), "capped at max_backoff_s"


def test_jitter_can_be_switched_off_for_a_deterministic_wait() -> None:
    recorder = _Recorder()
    transport = TransportSettings(
        max_attempts=3, initial_backoff_s=2.0, max_backoff_s=64.0, jitter=False
    )
    with pytest.raises(ModelProviderError):
        _run(_failing("service"), transport, recorder)
    assert recorder.waits == [2.0, 4.0]


def test_a_service_requested_wait_wins_over_our_backoff() -> None:
    """It knows its own load better than an exponential curve does."""
    recorder = _Recorder()
    transport = TransportSettings(
        max_attempts=2, initial_backoff_s=0.5, max_backoff_s=64.0, deadline_s=120.0
    )
    with pytest.raises(ModelProviderError):
        _run(_failing("rate_limit", retry_after=7.5), transport, recorder)
    assert recorder.waits == [7.5], "not jittered, and not our 0.5s backoff"


# -- the two bounds ----------------------------------------------------------


def test_the_deadline_overrides_a_service_requested_wait() -> None:
    """SC-017, and the rule most easily forgotten.

    A `Retry-After` longer than the remaining budget must fail on the deadline. If
    it were honoured, one response header could silently extend an extraction past
    the bound the caller set.
    """
    recorder = _Recorder()
    transport = TransportSettings(max_attempts=5, deadline_s=2.0)
    with pytest.raises(ModelProviderError) as caught:
        _run(_failing("rate_limit", retry_after=300.0), transport, recorder)
    assert caught.value.reason == "deadline"
    assert recorder.waits == [], "it must not sleep past the deadline"
    assert "overrides both our backoff" in str(caught.value)


def test_the_deadline_stops_a_long_backoff_chain() -> None:
    recorder = _Recorder()
    transport = TransportSettings(
        max_attempts=20, initial_backoff_s=30.0, max_backoff_s=60.0, deadline_s=5.0
    )
    with pytest.raises(ModelProviderError) as caught:
        _run(_failing("service"), transport, recorder)
    assert caught.value.reason == "deadline"
    assert recorder.waits == []


def test_the_attempt_limit_is_never_exceeded() -> None:
    """SC-017 — zero extractions make more attempts than configured."""
    for limit in (1, 2, 3, 7):
        call = _failing("service")
        with pytest.raises(ModelProviderError):
            _run(call, TransportSettings(max_attempts=limit, deadline_s=600.0))
        assert call.calls["n"] == limit


def test_the_error_carries_the_real_attempt_count() -> None:
    """A caller reading `attempts` is told the truth, not the default."""
    with pytest.raises(ModelProviderError) as caught:
        _run(_failing("timeout"), TransportSettings(max_attempts=4, deadline_s=600.0))
    assert caught.value.attempts == 4


def test_a_credential_that_expires_mid_retry_stops_on_that_attempt() -> None:
    """The spec's edge case: credentials expiring between attempts of one extraction.

    The retry loop's mapping table already gets this right, but "the table implies
    it" and "the behaviour is asserted" are different claims — and this is the case
    where a transient failure and a permanent one arrive in the same extraction, so
    a loop that classified once at the start rather than per attempt would keep
    retrying an expired credential until the budget ran out.
    """
    calls = {"n": 0}

    def call() -> ModelResponse:
        calls["n"] += 1
        reason = "service" if calls["n"] == 1 else "auth"
        raise ModelProviderError(
            f"attempt {calls['n']}: {reason}", reason=reason, adapter_id="probe"
        )

    with pytest.raises(ModelProviderError) as caught:
        _run(call, TransportSettings(max_attempts=5, deadline_s=600.0))

    assert calls["n"] == 2, "the expired credential must stop the loop, not exhaust the budget"
    assert caught.value.reason == "auth"
    assert caught.value.transient is False
    assert caught.value.attempts == 2, "and the count reports the attempt it actually stopped on"


def test_transport_settings_come_from_the_ingest_layer() -> None:
    """research.md R9 — one retry policy in the system, not two that drift."""
    from docdoc.ingest.options import TransportSettings as IngestSettings

    assert TransportSettings is IngestSettings
