"""Console accounts and sessions, against the in-memory fake.

Written against `InMemoryConsoleAccounts`, and every function here is
imported and re-run against `PostgresConsoleAccounts` by
`tests/integration/test_console_accounts_postgres.py`, so the two backends are
held to one behaviour rather than two hand-written test files that could drift
apart.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from docdoc.runs.console_accounts import (
    ConsoleAccountError,
    hash_password,
    verify_password,
)
from tests.fixtures.console_accounts import InMemoryConsoleAccounts

NOW = datetime(2026, 10, 20, 9, 0, tzinfo=UTC)
IDLE = timedelta(seconds=900)
SALT = b"s" * 16


@pytest.fixture
def accounts() -> InMemoryConsoleAccounts:
    return InMemoryConsoleAccounts()


def test_a_hash_verifies_only_its_own_password():
    encoded = hash_password("correct horse battery", salt=SALT)
    assert encoded.startswith("scrypt$16384$8$1$")
    assert verify_password("correct horse battery", encoded)
    assert not verify_password("correct horse batterz", encoded)


def test_add_refuses_bad_usernames_short_passwords_and_duplicates(accounts):
    for bad in ("Alice", "a b", "", "x" * 65):
        with pytest.raises(ConsoleAccountError):
            accounts.add(bad, "long enough pass", salt=SALT, now=NOW)
    with pytest.raises(ConsoleAccountError):
        accounts.add("alice", "short", salt=SALT, now=NOW)
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    with pytest.raises(ConsoleAccountError):
        accounts.add("alice", "long enough pass", salt=SALT, now=NOW)


def test_login_then_resolve_names_the_operator(accounts):
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    assert accounts.login(" Alice ", "long enough pass", token="t1", now=NOW)
    assert accounts.resolve("t1", now=NOW + timedelta(seconds=10), idle=IDLE) == "alice"
    assert accounts.resolve("unknown", now=NOW, idle=IDLE) is None


def test_an_idle_session_is_refused_and_removed(accounts):
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    accounts.login("alice", "long enough pass", token="t1", now=NOW)
    assert accounts.resolve("t1", now=NOW + IDLE, idle=IDLE) is None
    assert accounts.resolve("t1", now=NOW, idle=IDLE) is None  # gone, not merely stale


def test_activity_extends_the_session(accounts):
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    accounts.login("alice", "long enough pass", token="t1", now=NOW)
    accounts.resolve("t1", now=NOW + timedelta(seconds=800), idle=IDLE)
    assert accounts.resolve("t1", now=NOW + timedelta(seconds=1600), idle=IDLE) == "alice"


def test_five_failures_lock_for_five_minutes(accounts):
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    for _ in range(5):
        assert not accounts.login("alice", "wrong password!", token="x", now=NOW)
    assert not accounts.login(
        "alice", "long enough pass", token="t1", now=NOW + timedelta(minutes=4)
    )
    assert accounts.login("alice", "long enough pass", token="t2", now=NOW + timedelta(minutes=5))


def test_lock_expiry_resets_the_counter_so_one_more_failure_does_not_relock(accounts):
    """One typo right after a lock lapses must not immediately re-lock.

    Without resetting `failed_logins` to zero at the moment the lock is set,
    the counter sits at `MAX_FAILED_LOGINS` through the whole five minutes,
    and the first failure after it lapses reads as the sixth consecutive one
    — relocking at once. "Five consecutive failures" has to mean five fresh
    ones each time, not five, ever, with the clock merely pausing.
    """
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    for _ in range(5):
        assert not accounts.login("alice", "wrong password!", token="x", now=NOW)
    later = NOW + timedelta(minutes=5)
    assert not accounts.login("alice", "wrong password!", token="y", now=later)
    assert accounts.login("alice", "long enough pass", token="t", now=later)


def test_a_failure_during_a_lock_neither_clears_nor_extends_it(accounts):
    """A wrong password offered while already locked must change nothing.

    Not reset the lock early (which is the lockout-bypass: enough concurrent
    wrong passwords landing after the lock is set would otherwise clear it
    again and again, for unlimited guesses) and not extend it either. The
    refusal lasts exactly the original five minutes — no more, no less.
    """
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    for _ in range(5):
        assert not accounts.login("alice", "wrong password!", token="x", now=NOW)
    # One more wrong password, offered while still inside the original lock.
    assert not accounts.login("alice", "wrong password!", token="y", now=NOW + timedelta(minutes=4))
    # The lock is still exactly the original five minutes from NOW: refused
    # right up to it, and the correct password succeeds exactly at it.
    assert not accounts.login(
        "alice", "long enough pass", token="t1", now=NOW + timedelta(minutes=4, seconds=59)
    )
    assert accounts.login("alice", "long enough pass", token="t2", now=NOW + timedelta(minutes=5))


def test_logout_ends_a_session(accounts):
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    accounts.login("alice", "long enough pass", token="t1", now=NOW)
    accounts.logout("t1")
    assert accounts.resolve("t1", now=NOW, idle=IDLE) is None


def test_logout_of_an_unknown_token_is_a_no_op(accounts):
    accounts.logout("never issued")  # does not raise


def test_disable_is_false_for_an_unknown_or_already_disabled_account(accounts):
    assert accounts.disable("nobody", now=NOW) is False
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    assert accounts.disable("alice", now=NOW) is True
    assert accounts.disable("alice", now=NOW) is False


def test_set_password_on_an_unknown_user_raises(accounts):
    with pytest.raises(ConsoleAccountError):
        accounts.set_password("nobody", "long enough pass", salt=SALT, now=NOW)


def test_disable_and_passwd_end_live_sessions(accounts):
    accounts.add("alice", "long enough pass", salt=SALT, now=NOW)
    accounts.add("bob", "long enough pass", salt=SALT, now=NOW)
    accounts.login("alice", "long enough pass", token="a", now=NOW)
    accounts.login("bob", "long enough pass", token="b", now=NOW)
    assert accounts.disable("alice", now=NOW) is True
    accounts.set_password("bob", "another long pass", salt=SALT, now=NOW)
    assert accounts.resolve("a", now=NOW, idle=IDLE) is None
    assert accounts.resolve("b", now=NOW, idle=IDLE) is None
    assert not accounts.login("alice", "long enough pass", token="a2", now=NOW)
