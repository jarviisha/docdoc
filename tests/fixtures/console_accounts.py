"""An in-memory `ConsoleAccounts`, so account and session policy needs no database.

The same reasoning `tests/fixtures/run_queue.py` states for `InMemoryRunQueue`:
login, lockout, and idle expiry are questions about *rules*, and a test that
needs Postgres to ask them is a test most contributors will not run.
`tests/integration/test_console_accounts_postgres.py` imports the test
functions written against this fixture and runs them again against
`PostgresConsoleAccounts`, so the two are held to one behaviour by
construction rather than by two hand-written test files drifting apart.

Shares no code and no base class with `PostgresConsoleAccounts` — only the
`ConsoleAccounts` shape and the module-level policy functions
(`locked`, `after_failure`, `alive`) it calls, exactly as that module intends.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from docdoc.runs.console_accounts import (
    ConsoleAccount,
    ConsoleAccountError,
    after_failure,
    alive,
    disabled_account_message,
    hash_password,
    locked,
    normalise_username,
    validate_password,
    validate_username,
    verify_dummy_password,
    verify_password,
)
from docdoc.runs.principal import digest_of

if TYPE_CHECKING:
    from datetime import datetime, timedelta

__all__ = ["InMemoryConsoleAccounts"]


@dataclass
class _User:
    password_hash: str
    created_at: datetime
    disabled_at: datetime | None = None
    failed_logins: int = 0
    locked_until: datetime | None = None


@dataclass
class _Session:
    username: str
    created_at: datetime
    last_seen_at: datetime


@dataclass
class InMemoryConsoleAccounts:
    """Satisfies `docdoc.runs.console_accounts.ConsoleAccounts`."""

    _users: dict[str, _User] = field(default_factory=dict)
    _sessions: dict[str, _Session] = field(default_factory=dict)

    def add(self, username: str, password: str, *, salt: bytes, now: datetime) -> ConsoleAccount:
        validate_username(username)
        validate_password(password)
        existing = self._users.get(username)
        if existing is None:
            self._users[username] = _User(
                password_hash=hash_password(password, salt=salt), created_at=now
            )
            return ConsoleAccount(username=username, created_at=now, disabled_at=None)
        if existing.disabled_at is None:
            raise ConsoleAccountError(f"a console account named {username!r} already exists")
        existing.password_hash = hash_password(password, salt=salt)
        existing.disabled_at = None
        existing.failed_logins = 0
        existing.locked_until = None
        return ConsoleAccount(username=username, created_at=existing.created_at, disabled_at=None)

    def set_password(self, username: str, password: str, *, salt: bytes, now: datetime) -> None:
        validate_password(password)
        user = self._users.get(username)
        if user is None:
            raise ConsoleAccountError(f"no console account named {username!r}")
        if user.disabled_at is not None:
            raise ConsoleAccountError(disabled_account_message(username))
        user.password_hash = hash_password(password, salt=salt)
        user.failed_logins = 0
        user.locked_until = None
        self._drop_sessions_for(username)

    def disable(self, username: str, *, now: datetime) -> bool:
        user = self._users.get(username)
        if user is None:
            raise ConsoleAccountError(f"no console account named {username!r}")
        if user.disabled_at is not None:
            return False
        user.disabled_at = now
        self._drop_sessions_for(username)
        return True

    def accounts(self) -> tuple[ConsoleAccount, ...]:
        return tuple(
            ConsoleAccount(username=name, created_at=user.created_at, disabled_at=user.disabled_at)
            for name, user in sorted(self._users.items())
        )

    def login(self, username: str, password: str, *, token: str, now: datetime) -> bool:
        normalised = normalise_username(username)
        user = self._users.get(normalised)
        if user is None:
            verify_dummy_password(password)  # timing only; no such account
            return False
        if user.disabled_at is not None:
            verify_dummy_password(password)  # timing only; account is disabled
            return False
        if locked(user.failed_logins, user.locked_until, now):
            verify_dummy_password(password)  # timing only; account is locked
            return False

        if verify_password(password, user.password_hash):
            user.failed_logins = 0
            user.locked_until = None
            self._sessions[digest_of(token)] = _Session(
                username=normalised, created_at=now, last_seen_at=now
            )
            return True

        user.failed_logins, user.locked_until = after_failure(user.failed_logins, now)
        return False

    def resolve(self, token: str, *, now: datetime, idle: timedelta) -> str | None:
        digest = digest_of(token)
        session = self._sessions.get(digest)
        if session is None:
            return None
        user = self._users.get(session.username)
        if (
            user is None
            or user.disabled_at is not None
            or not alive(session.last_seen_at, now, idle)
        ):
            del self._sessions[digest]
            return None
        session.last_seen_at = now
        return session.username

    def logout(self, token: str) -> None:
        self._sessions.pop(digest_of(token), None)

    def _drop_sessions_for(self, username: str) -> None:
        for digest, session in list(self._sessions.items()):
            if session.username == username:
                del self._sessions[digest]
