"""Console accounts and sessions, against a real database.

Imports every test function from `tests/unit/test_console_accounts.py` and
re-runs it against `PostgresConsoleAccounts` through the same `accounts`
fixture name those functions already take as a parameter — pytest resolves
the fixture from *this* module, so the bodies are identical and the backend
is the only thing that changed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.infra import require_database

# Re-run every behaviour test against the Postgres backend. `test_a_hash_...`
# is omitted: it exercises `hash_password`/`verify_password` directly and
# takes no `accounts` fixture, so importing it here would just run it twice.
from tests.unit.test_console_accounts import (  # noqa: F401
    test_activity_extends_the_session,
    test_add_refuses_bad_usernames_short_passwords_and_duplicates,
    test_an_idle_session_is_refused_and_removed,
    test_disable_and_passwd_end_live_sessions,
    test_disable_is_false_for_an_unknown_or_already_disabled_account,
    test_five_failures_lock_for_five_minutes,
    test_lock_expiry_resets_the_counter_so_one_more_failure_does_not_relock,
    test_login_then_resolve_names_the_operator,
    test_logout_ends_a_session,
    test_logout_of_an_unknown_token_is_a_no_op,
    test_set_password_on_an_unknown_user_raises,
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
