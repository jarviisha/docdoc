"""Console accounts and sessions, against a real database.

Imports every test function from `tests/unit/test_console_accounts.py` and
re-runs it against `PostgresConsoleAccounts` through the same `accounts`
fixture name those functions already take as a parameter — pytest resolves
the fixture from *this* module, so the bodies are identical and the backend
is the only thing that changed.

One test lives only here: `test_concurrent_wrong_passwords_still_lock_the_account`
fires real concurrent requests on real connections, which the in-memory fake
cannot do meaningfully (it has no connection to race) and which is the one
thing `tests/unit/test_console_accounts.py`'s sequential tests structurally
cannot exercise.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta

import pytest
from tests.infra import require_database

# Re-run every behaviour test against the Postgres backend. `test_a_hash_...`
# is omitted: it exercises `hash_password`/`verify_password` directly and
# takes no `accounts` fixture, so importing it here would just run it twice.
from tests.unit.test_console_accounts import (  # noqa: F401
    NOW,
    SALT,
    test_a_failure_during_a_lock_neither_clears_nor_extends_it,
    test_activity_extends_the_session,
    test_add_refuses_bad_usernames_short_passwords_and_duplicates,
    test_add_revives_a_disabled_account_with_the_new_password,
    test_an_idle_session_is_refused_and_removed,
    test_disable_and_passwd_end_live_sessions,
    test_disable_raises_for_an_unknown_user_and_is_false_when_already_disabled,
    test_five_failures_lock_for_five_minutes,
    test_lock_expiry_resets_the_counter_so_one_more_failure_does_not_relock,
    test_login_then_resolve_names_the_operator,
    test_logout_ends_a_session,
    test_logout_of_an_unknown_token_is_a_no_op,
    test_set_password_clears_a_lock,
    test_set_password_on_a_disabled_account_raises_naming_add,
    test_set_password_on_an_unknown_user_raises,
    test_set_password_resets_the_failure_counter,
)

from docdoc.runs import migrations
from docdoc.runs.console_accounts import PostgresConsoleAccounts
from docdoc.runs.postgres import PostgresRunQueue

pytestmark = pytest.mark.postgres


@pytest.fixture
def accounts() -> PostgresConsoleAccounts:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE console_sessions, console_users")
    queue = PostgresRunQueue(lambda: psycopg.connect(dsn))
    return PostgresConsoleAccounts(execute=queue._execute)


def test_concurrent_wrong_passwords_still_lock_the_account(
    accounts: PostgresConsoleAccounts,
) -> None:
    """The lockout-bypass this fix round closes, exercised for real.

    Each `accounts.login(...)` call opens its own connection
    (`PostgresRunQueue._execute`), so firing several from separate threads is
    several genuinely concurrent requests against the same row — the shape
    the stale-read race needed and that a single Python process calling the
    same object sequentially cannot produce.

    More attempts than `MAX_FAILED_LOGINS` (12, not 5): the bug this guards
    against was exactly a batch running *past* the fifth failure clearing the
    lock the fifth one set, so stopping at 5 would not have caught it.
    """
    accounts.add("carol", "long enough pass", salt=SALT, now=NOW)

    results: list[bool] = []
    lock = threading.Lock()

    def attempt(index: int) -> None:
        result = accounts.login("carol", "wrong password!", token=f"concurrent-{index}", now=NOW)
        with lock:
            results.append(result)

    threads = [threading.Thread(target=attempt, args=(i,)) for i in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 12
    assert not any(results), "a wrong password must never succeed"

    # The account must now be locked: the correct password, at the same
    # instant, is refused -- not merely one more wrong-password failure.
    assert not accounts.login("carol", "long enough pass", token="correct-while-locked", now=NOW)

    # And it recovers once the lock lapses, which confirms the account was
    # locked for the right reason rather than left in some other broken state.
    assert accounts.login(
        "carol", "long enough pass", token="correct-after-lock", now=NOW + timedelta(minutes=5)
    )
