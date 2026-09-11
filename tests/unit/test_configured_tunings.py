"""The tuning accessors this milestone added, and their one precedence rule.

Nine functions, one shape: **explicit, then environment, then a default**, and a
refusal for an explicit value that is not positive. They are three lines each,
which is exactly why none of them was tested — and why an inverted precedence or
a default read from the wrong constant would have gone unnoticed. A delivery
attempt limit that silently fell back to the environment while an operator
passed `--delivery-attempts 1` is six outbound requests they said not to make.

`DOCDOC_`-prefixed throughout, so `conftest.py`'s sweep already clears them and
each test sets only the one it is about.
"""

from __future__ import annotations

from datetime import timedelta

import pytest

from docdoc.runs import identity, maintenance

#: Each accessor with the variable it reads and the value it falls back to. A
#: table rather than nine near-identical tests, because the property under test
#: is that they all behave the same way.
ACCESSORS = [
    (
        identity.configured_delivery_attempts,
        identity.DELIVERY_ATTEMPTS_ENV,
        identity.DEFAULT_DELIVERY_ATTEMPTS,
    ),
    (
        identity.configured_delivery_timeout,
        identity.DELIVERY_TIMEOUT_ENV,
        identity.DEFAULT_DELIVERY_TIMEOUT_SECONDS,
    ),
    (
        identity.configured_delivery_backoff,
        identity.DELIVERY_BACKOFF_ENV,
        identity.DEFAULT_DELIVERY_BACKOFF_SECONDS,
    ),
    (
        identity.configured_delivery_batch,
        identity.DELIVERY_BATCH_ENV,
        identity.DEFAULT_DELIVERY_BATCH,
    ),
    (
        identity.configured_credential_ttl,
        identity.CREDENTIAL_TTL_ENV,
        identity.DEFAULT_CREDENTIAL_TTL_SECONDS,
    ),
    (
        identity.configured_maintenance_interval,
        identity.MAINTENANCE_INTERVAL_ENV,
        maintenance.DEFAULT_INTERVAL_SECONDS,
    ),
    (
        identity.configured_maintenance_budget,
        identity.MAINTENANCE_BUDGET_ENV,
        maintenance.DEFAULT_BUDGET_MS,
    ),
]

IDS = [accessor.__name__ for accessor, _, _ in ACCESSORS]


@pytest.mark.parametrize(("accessor", "variable", "default"), ACCESSORS, ids=IDS)
def test_nothing_configured_gives_the_documented_default(accessor, variable, default) -> None:
    assert accessor() == default


@pytest.mark.parametrize(("accessor", "variable", "default"), ACCESSORS, ids=IDS)
def test_the_environment_is_read_when_nothing_is_passed(
    accessor, variable, default, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(variable, "7")

    assert accessor() == 7


@pytest.mark.parametrize(("accessor", "variable", "default"), ACCESSORS, ids=IDS)
def test_an_explicit_value_outranks_the_environment(
    accessor, variable, default, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The precedence that matters: a flag an operator typed wins.

    The inverted version would look correct in every deployment that sets no
    variable, which is most of them.
    """
    monkeypatch.setenv(variable, "7")

    assert accessor(3) == 3


@pytest.mark.parametrize(("accessor", "variable", "default"), ACCESSORS, ids=IDS)
def test_an_unreadable_environment_value_falls_back_rather_than_raising(
    accessor, variable, default, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Absent and unreadable are one answer.

    A process that refused to start on a typo in a *tuning* variable would turn
    a cosmetic mistake into an outage; a flag the operator typed is a different
    case, and the test below is that one.
    """
    monkeypatch.setenv(variable, "not a number")

    assert accessor() == default


@pytest.mark.parametrize(("accessor", "variable", "default"), ACCESSORS, ids=IDS)
def test_an_explicit_non_positive_value_is_refused_by_flag_name(
    accessor, variable, default
) -> None:
    """Zero and below are refused loudly, because they were typed on purpose."""
    with pytest.raises(ValueError, match="must be a positive integer"):
        accessor(0)


# -- the two that are not integers -------------------------------------------


def test_correction_retention_is_absent_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """``None`` means "as long as the run" — not "for ever" and not "immediately"."""
    assert identity.configured_correction_retention() is None


def test_correction_retention_reads_days(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(identity.CORRECTION_RETENTION_DAYS_ENV, "90")

    assert identity.configured_correction_retention() == timedelta(days=90)
    assert identity.configured_correction_retention(5) == timedelta(days=5)


def test_the_ssrf_guard_stays_on_for_anything_but_an_affirmative(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-060. A guard a typo could disable would be a guard.

    Named for the capability it grants rather than the guard it removes, so an
    operator reading a variable list sees what they are permitting.
    """
    assert identity.delivery_allows_private() is False

    for affirmative in ("1", "true", "YES", " on "):
        monkeypatch.setenv(identity.DELIVERY_ALLOW_PRIVATE_ENV, affirmative)
        assert identity.delivery_allows_private() is True, affirmative

    for anything_else in ("0", "no", "sure", "ture", ""):
        monkeypatch.setenv(identity.DELIVERY_ALLOW_PRIVATE_ENV, anything_else)
        assert identity.delivery_allows_private() is False, anything_else
