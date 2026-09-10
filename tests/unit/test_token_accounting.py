"""`_record_tokens` — the token budget's bookkeeping, and what it swallows.

**Counted after the run, which is the whole design.** Tokens are known only once
a provider has answered, so this can refuse the *next* submission and can never
abort the one that produced them. Enforcing mid-run would discard work already
paid for, which is the failure Milestone 9 was built to remove (FR-047).

None of it was tested. The three things that matter here are all silences — a
`None` limiter, a result with no usage, a counter write that failed — and a
silence is exactly what a test suite does not notice going wrong. A version that
raised on the third would turn a completed run into a failed one for a
bookkeeping write, which is the most expensive possible way to lose a document.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import pytest

from docdoc.runs.worker import _record_tokens

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)


@dataclass
class _Run:
    tenant_id: str = "acme"


@dataclass
class _Usage:
    input_tokens: int | None = 0
    output_tokens: int | None = 0


@dataclass
class _Provenance:
    usage: _Usage | None = None


@dataclass
class _Extraction:
    provenance: _Provenance | None = None


@dataclass
class _Result:
    extraction: _Extraction | None = None


def _result(input_tokens: int | None = 0, output_tokens: int | None = 0) -> _Result:
    return _Result(_Extraction(_Provenance(_Usage(input_tokens, output_tokens))))


class _Limiter:
    def __init__(self, raises: Exception | None = None):
        self.recorded: list[dict[str, Any]] = []
        self._raises = raises

    def record_tokens(self, *, tenant_id: str, tokens: int, now: datetime) -> None:
        if self._raises is not None:
            raise self._raises
        self.recorded.append({"tenant_id": tenant_id, "tokens": tokens, "now": now})


def test_both_directions_are_counted() -> None:
    """Input and output. A budget that counted one would be off by whatever
    ratio the schema happened to produce."""
    limiter = _Limiter()

    _record_tokens(limiter, _Run(), _result(1200, 300), now=NOW)

    assert limiter.recorded == [{"tenant_id": "acme", "tokens": 1500, "now": NOW}]


def test_no_limiter_counts_nothing() -> None:
    """The default. A deployment that configured no limits counts nothing and
    behaves exactly as Milestone 9 did (FR-049)."""
    _record_tokens(None, _Run(), _result(1200, 300), now=NOW)


@pytest.mark.parametrize(
    "result",
    [_Result(), _Result(_Extraction()), _Result(_Extraction(_Provenance())), _result(0, 0)],
    ids=["no extraction", "no provenance", "no usage", "zero"],
)
def test_a_run_that_reports_no_usage_writes_no_counter(result: _Result) -> None:
    """Four shapes, and every one of them is a real result.

    An offline parse reports no usage at all; a cached extraction reports zero.
    Writing a zero row for either would make `limit_counters` grow with rows that
    refuse nothing — the accumulation the limits were configured to prevent,
    arriving by the back door.
    """
    limiter = _Limiter()

    _record_tokens(limiter, _Run(), result, now=NOW)

    assert limiter.recorded == []


def test_a_none_in_either_direction_is_read_as_zero() -> None:
    """A provider that reports one and not the other. `None + int` would raise
    here, inside the `try` — so it would be swallowed, and the run's tokens would
    silently never be counted."""
    limiter = _Limiter()

    _record_tokens(limiter, _Run(), _result(None, 300), now=NOW)

    assert limiter.recorded[0]["tokens"] == 300


def test_a_counter_that_cannot_be_written_does_not_fail_the_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The silence that matters most.

    The next tick's counter being short by one run is a smaller error than
    losing the run, and the two are not close.
    """
    with caplog.at_level("WARNING", logger="docdoc.runs"):
        _record_tokens(_Limiter(RuntimeError("gone")), _Run(), _result(10, 10), now=NOW)

    assert any("limit.tokens_unrecorded" in record.getMessage() for record in caplog.records), (
        "it swallowed the failure without saying so, which is the version that "
        "makes a customer's budget quietly wrong"
    )
