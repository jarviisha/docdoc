"""Console accounts and sessions: who may sign in, and for how long (ADR-0023 §1).

An operator authenticates with a username and password; there is no other
console credential. A username matches `USERNAME_PATTERN` and is compared the
same way it was created — `normalise_username` lower-cases and trims what
`login` is given, and `add` takes the username literally, so an account is
only ever created already in the form login will look for. A password is
never stored: `hash_password` runs `hashlib.scrypt` over it and only the
derived key is kept, in the self-describing form
``scrypt$16384$8$1$<salt>$<key>`` so a deployment that tunes the cost
parameters later can still verify an older row.

Five consecutive failed logins lock a username for five minutes (`LOCKOUT`,
imported rather than constructed — see the note on it in
`docdoc.runs.identity`), and the three policy questions this buys — is an
account locked, what a failure does to that lock, and whether a session is
still alive — are `locked`, `after_failure`, and `alive`: free functions, so
the in-memory fixture calls one definition of the policy rather than carrying
its own. `PostgresConsoleAccounts` cannot call them from inside a SQL
statement — and has to, because computing a failure's effect in Python from a
value read a statement earlier is exactly the stale-read race that let N
concurrent wrong passwords all land on "1" instead of counting up to the
lockout. `_record_failure` and `_open_session_if_still_verified` restate the
same rule as one atomic `UPDATE`/`INSERT` apiece instead, each pointing back at
the function it must stay in sync with.

A session is a bearer token (`docdoc.runs.identity.new_console_token`); only
`digest_of(token)` is ever stored, exactly as a credential's key never is
(`docdoc.runs.keys`). `resolve` is where the idle bound is enforced — the
clock is the server's, not the cookie's claimed age — and an idle or
disabled-account session is deleted on the read that discovers it, not merely
reported stale.

**Nothing here reads a clock or a random source.** `now`, `salt`, and `token`
arrive as parameters from `docdoc.runs.identity`, which is the only module in
this package permitted to read either
(`tests/unit/test_runs_clock_confinement.py`, FR-096a).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from docdoc.runs.identity import LOCKOUT
from docdoc.runs.principal import digest_of

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime, timedelta

__all__ = [
    "LOCKOUT",
    "MAX_FAILED_LOGINS",
    "MIN_PASSWORD_LENGTH",
    "USERNAME_PATTERN",
    "ConsoleAccount",
    "ConsoleAccountError",
    "ConsoleAccounts",
    "PostgresConsoleAccounts",
    "after_failure",
    "alive",
    "hash_password",
    "locked",
    "normalise_username",
    "validate_password",
    "validate_username",
    "verify_dummy_password",
    "verify_password",
]

#: ADR-0023 §1. Narrow enough that a username is always a safe identifier: no
#: separator, no case a comparison could fold differently, no character that
#: would need escaping anywhere it is logged.
USERNAME_PATTERN = re.compile(r"^[a-z0-9_.-]{1,64}$")

#: ADR-0023 §1's floor. Short of a real strength estimator, which this project
#: has no dependency for and does not need one for an operator population
#: measured in the tens.
MIN_PASSWORD_LENGTH = 12

#: How many consecutive failures lock a username (ADR-0023 §1).
MAX_FAILED_LOGINS = 5

#: The scrypt cost parameters. `N=16384, r=8, p=1` is the parameter set
#: `hashlib`'s own documentation recommends for an interactive login; a
#: deployment that wants a stronger (slower) one moves these three numbers and
#: every row stays verifiable because the parameters travel with the hash.
_SCRYPT_N = 16384
_SCRYPT_R = 8
_SCRYPT_P = 1


class ConsoleAccountError(ValueError):
    """A console account operation was refused: a bad username, a short
    password, a duplicate, or an unknown account."""


@dataclass(frozen=True)
class ConsoleAccount:
    """One operator, as everything a listing needs and nothing a session does.

    No password hash and no session token — `accounts()` exists so an
    operator can see who has access, and either field would turn that listing
    into a way to obtain one.
    """

    username: str
    created_at: datetime
    disabled_at: datetime | None


def normalise_username(raw: str) -> str:
    """How a username is compared, every time it is given to `login`.

    Trimmed and lower-cased so ``" Alice "`` resolves the account created as
    ``"alice"``. `add` does *not* call this — an account is created with the
    username it is given, and `USERNAME_PATTERN` already forbids the upper
    case and whitespace this would otherwise silently absorb.
    """
    return raw.strip().lower()


def hash_password(password: str, *, salt: bytes) -> str:
    """``scrypt$16384$8$1$<b64 salt>$<b64 key>``, the only thing ever stored.

    The parameters are in the string, not assumed from a constant, so a
    deployment that tunes them later can still verify a row hashed under the
    old ones.
    """
    key = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P)
    encoded_salt = base64.b64encode(salt).decode("ascii")
    encoded_key = base64.b64encode(key).decode("ascii")
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${encoded_salt}${encoded_key}"


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time, and tolerant of a hash this function cannot parse.

    A malformed `encoded` — the wrong number of fields, a non-scrypt
    algorithm tag — answers `False` rather than raising, which is what lets
    `login` run this against the module-level dummy hash for an unknown
    username without a second code path for "this isn't really a password
    hash".
    """
    parts = encoded.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    _, n, r, p, encoded_salt, encoded_key = parts
    try:
        salt = base64.b64decode(encoded_salt)
        expected = base64.b64decode(encoded_key)
        candidate = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=int(n), r=int(r), p=int(p)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


#: A hash of a password nobody will ever type, so an unknown username, a
#: disabled account, and a locked account each cost `login` the same
#: `verify_password` call a wrong password on a real account does (ADR-0023
#: §1's four indistinguishable refusals, held at the timing layer too, not
#: only in the response body). Computed once, at import time, from a fixed
#: salt — this value is never compared against a real account and never
#: written anywhere.
_DUMMY_HASH = hash_password("no account is ever issued this password", salt=b"\x00" * 16)


def verify_dummy_password(password: str) -> None:
    """Spend the cost a real `verify_password` call has, and keep nothing.

    Called by both backends — `PostgresConsoleAccounts` and
    `tests/fixtures/console_accounts.py`'s `InMemoryConsoleAccounts` — for an
    unknown username, a disabled account, and a locked account, so none of
    the three answers `login` gives before it even reaches a password
    comparison is faster than a wrong password on a real, unlocked account.
    One dummy hash shared by both, rather than two, is the same reasoning
    `locked`/`after_failure`/`alive` are shared functions.
    """
    verify_password(password, _DUMMY_HASH)


def validate_username(username: str) -> None:
    """Raise `ConsoleAccountError` unless `username` matches `USERNAME_PATTERN`.

    Public, and called by `add` in both backends, so the rule a username must
    satisfy is written once rather than copied into each store.
    """
    if not USERNAME_PATTERN.fullmatch(username):
        raise ConsoleAccountError(
            f"{username!r} is not a valid console username; it must match "
            f"{USERNAME_PATTERN.pattern!r}"
        )


def validate_password(password: str) -> None:
    """Raise `ConsoleAccountError` unless `password` meets `MIN_PASSWORD_LENGTH`.

    Public, and called by `add` and `set_password` in both backends.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ConsoleAccountError(
            f"a console password must be at least {MIN_PASSWORD_LENGTH} characters"
        )


def locked(failed_logins: int, locked_until: datetime | None, now: datetime) -> bool:
    """Whether a username is currently refused for having failed too often.

    `failed_logins` is accepted for symmetry with `after_failure` and is not
    read: the lock is `locked_until` alone, which is what lets a lock outlive
    the counter that produced it — nothing here resets `locked_until` early.
    """
    return locked_until is not None and now < locked_until


def after_failure(failed_logins: int, now: datetime) -> tuple[int, datetime | None]:
    """The counter and lock a failed login produces, as one pure function.

    The failure that *reaches* `MAX_FAILED_LOGINS` both locks **and resets the
    counter to zero**. Without the reset, the counter would sit at
    `MAX_FAILED_LOGINS` through the whole lockout, and the first failure after
    it lapses would read as the *sixth* consecutive one and relock
    immediately — turning "five consecutive failures lock for five minutes"
    into "five failures, ever, with the clock merely pausing every five
    minutes". Resetting to zero is what makes five *fresh* failures the
    requirement again once the lock expires.

    A caller that is already locked does not reach here at all (`login`
    returns before counting one), so a correct password offered while locked
    neither resets nor extends the five minutes.

    `PostgresConsoleAccounts._record_failure` encodes this exact rule in SQL,
    because a database statement cannot call this function — keep the two in
    sync if this policy ever changes.
    """
    count = failed_logins + 1
    if count >= MAX_FAILED_LOGINS:
        return 0, now + LOCKOUT
    return count, None


def alive(last_seen_at: datetime, now: datetime, idle: timedelta) -> bool:
    """Whether a session is still within its idle bound.

    Strict less-than: a session touched exactly `idle` ago is refused, which
    is what makes `idle` a bound an operator can state as a number rather than
    "a little more than".
    """
    return now - last_seen_at < idle


class ConsoleAccounts(Protocol):
    """What a console accounts store does, implemented by two backends.

    `PostgresConsoleAccounts` below and `InMemoryConsoleAccounts`
    (`tests/fixtures/console_accounts.py`) share no code and no base class —
    only this shape and the three policy functions above, which is what lets
    the same test functions run against both (brief Step 6).
    """

    def add(self, username: str, password: str, *, salt: bytes, now: datetime) -> ConsoleAccount:
        """A new account. Raises on a bad username, a short password, or a
        duplicate."""
        ...

    def set_password(self, username: str, password: str, *, salt: bytes, now: datetime) -> None:
        """Replace an account's password. Raises on an unknown user; deletes
        that user's sessions."""
        ...

    def disable(self, username: str, *, now: datetime) -> bool:
        """`True` if the account was active. Deletes that user's sessions."""
        ...

    def accounts(self) -> tuple[ConsoleAccount, ...]:
        """Every account, so an operator can see who has access."""
        ...

    def login(self, username: str, password: str, *, token: str, now: datetime) -> bool:
        """Whether `username`/`password` is correct and the account usable.

        On success stores a session under `digest_of(token)` and resets the
        failure counter. On failure counts and, past `MAX_FAILED_LOGINS`,
        locks.
        """
        ...

    def resolve(self, token: str, *, now: datetime, idle: timedelta) -> str | None:
        """The username behind a live session, touching `last_seen_at`.

        An idle session, or one whose account is disabled, is deleted rather
        than merely reported absent — the next `resolve` of the same token
        finds nothing either.
        """
        ...

    def logout(self, token: str) -> None:
        """End one session. A logout of an unknown token is a no-op."""
        ...


@dataclass
class PostgresConsoleAccounts:
    """`console_users` and `console_sessions`, behind the shape above.

    `execute` has the same shape `PostgresKeyStore.execute` does: a callable
    taking `(sql, params, *, fetch=None|"one"|"all")`, dict rows.
    """

    execute: Callable[..., Any]

    def add(self, username: str, password: str, *, salt: bytes, now: datetime) -> ConsoleAccount:
        validate_username(username)
        validate_password(password)
        row = self.execute(
            "INSERT INTO console_users (username, password_hash, created_at) "
            "VALUES (%s, %s, %s) "
            "ON CONFLICT (username) DO NOTHING "
            "RETURNING username, created_at, disabled_at",
            (username, hash_password(password, salt=salt), now),
            fetch="one",
        )
        if row is None:
            raise ConsoleAccountError(f"a console account named {username!r} already exists")
        return ConsoleAccount(
            username=row["username"], created_at=row["created_at"], disabled_at=row["disabled_at"]
        )

    def set_password(self, username: str, password: str, *, salt: bytes, now: datetime) -> None:
        validate_password(password)
        rows = self.execute(
            "UPDATE console_users SET password_hash = %s WHERE username = %s RETURNING username",
            (hash_password(password, salt=salt), username),
            fetch="all",
        )
        if not rows:
            raise ConsoleAccountError(f"no console account named {username!r}")
        self.execute("DELETE FROM console_sessions WHERE username = %s", (username,))

    def disable(self, username: str, *, now: datetime) -> bool:
        rows = self.execute(
            "UPDATE console_users SET disabled_at = %s "
            "WHERE username = %s AND disabled_at IS NULL "
            "RETURNING username",
            (now, username),
            fetch="all",
        )
        if not rows:
            return False
        self.execute("DELETE FROM console_sessions WHERE username = %s", (username,))
        return True

    def accounts(self) -> tuple[ConsoleAccount, ...]:
        rows = self.execute(
            "SELECT username, created_at, disabled_at FROM console_users ORDER BY username",
            (),
            fetch="all",
        )
        return tuple(
            ConsoleAccount(
                username=row["username"],
                created_at=row["created_at"],
                disabled_at=row["disabled_at"],
            )
            for row in rows or ()
        )

    def login(self, username: str, password: str, *, token: str, now: datetime) -> bool:
        normalised = normalise_username(username)
        row = self.execute(
            "SELECT password_hash, disabled_at, failed_logins, locked_until "
            "FROM console_users WHERE username = %s",
            (normalised,),
            fetch="one",
        )
        if row is None:
            verify_dummy_password(password)  # timing only; no such account
            return False
        if row["disabled_at"] is not None:
            verify_dummy_password(password)  # timing only; account is disabled
            return False
        if locked(row["failed_logins"], row["locked_until"], now):
            verify_dummy_password(password)  # timing only; account is locked
            return False

        if not verify_password(password, row["password_hash"]):
            self._record_failure(normalised, now=now)
            return False

        opened = self._open_session_if_still_verified(
            normalised, row["password_hash"], token=token, now=now
        )
        if not opened:
            # The row this password was checked against is not the row this
            # would commit against: the password changed, the account was
            # disabled, or it was relocked, in the gap between the read above
            # and here. That is the same answer a wrong password gets, not a
            # silent no-op (closes the set_password/disable race).
            self._record_failure(normalised, now=now)
            return False
        return True

    def _open_session_if_still_verified(
        self, username: str, verified_password_hash: str, *, token: str, now: datetime
    ) -> bool:
        """Commit the login as one statement, re-checking what `login` just checked.

        `login` reads the row, verifies the password against it, and only then
        calls this — two statements with a gap between them, and anything can
        change the account in that gap: `set_password`, `disable`, or another
        `login` locking it. Re-reading here would still leave a gap, so instead
        this re-states every precondition inside the statement that writes the
        session: still this `password_hash`, still not disabled, still not
        locked. `FOR UPDATE` takes the row lock before any of that is decided,
        so a concurrent `disable`/`set_password` (each a single `UPDATE`, which
        always takes the row lock) serializes against this rather than racing
        it. Zero rows back means the precondition failed; `login` treats that
        exactly as a wrong password.
        """
        row = self.execute(
            "WITH verified AS ("
            "  SELECT username FROM console_users "
            "  WHERE username = %(username)s AND password_hash = %(password_hash)s "
            "    AND disabled_at IS NULL "
            "    AND (locked_until IS NULL OR locked_until <= %(now)s) "
            "  FOR UPDATE"
            "), reset AS ("
            "  UPDATE console_users SET failed_logins = 0, locked_until = NULL "
            "  WHERE username IN (SELECT username FROM verified) "
            "  RETURNING username"
            ") "
            "INSERT INTO console_sessions (digest, username, created_at, last_seen_at) "
            "SELECT %(digest)s, username, %(now)s, %(now)s FROM reset "
            "ON CONFLICT (digest) DO UPDATE SET "
            "username = EXCLUDED.username, "
            "created_at = EXCLUDED.created_at, "
            "last_seen_at = EXCLUDED.last_seen_at "
            "RETURNING username",
            {
                "username": username,
                "password_hash": verified_password_hash,
                "now": now,
                "digest": digest_of(token),
            },
            fetch="one",
        )
        return row is not None

    def _record_failure(self, username: str, *, now: datetime) -> None:
        """One atomic `UPDATE` encoding exactly the rule `after_failure` does.

        A database statement cannot call a Python function, so the arithmetic
        is restated here — and restated so that it reads `failed_logins` off
        the row at the moment this `UPDATE` takes the row lock, not off a
        value `login` read earlier in the request. That is what makes N
        concurrent wrong passwords serialize into 1, 2, 3, ... N rather than
        all reading the same stale count and all writing 1, which is the
        lockout-bypass this method exists to close. Keep this in sync with
        `after_failure` if that policy changes.
        """
        self.execute(
            "UPDATE console_users SET "
            "failed_logins = CASE WHEN failed_logins + 1 >= %(max)s THEN 0 "
            "ELSE failed_logins + 1 END, "
            "locked_until = CASE WHEN failed_logins + 1 >= %(max)s THEN %(locked_until)s "
            "ELSE NULL END "
            "WHERE username = %(username)s",
            {"max": MAX_FAILED_LOGINS, "locked_until": now + LOCKOUT, "username": username},
        )

    def resolve(self, token: str, *, now: datetime, idle: timedelta) -> str | None:
        digest = digest_of(token)
        row = self.execute(
            "SELECT console_sessions.username AS username, "
            "console_sessions.last_seen_at AS last_seen_at, "
            "console_users.disabled_at AS disabled_at "
            "FROM console_sessions "
            "JOIN console_users ON console_users.username = console_sessions.username "
            "WHERE console_sessions.digest = %s",
            (digest,),
            fetch="one",
        )
        if row is None:
            return None
        if row["disabled_at"] is not None or not alive(row["last_seen_at"], now, idle):
            self.execute("DELETE FROM console_sessions WHERE digest = %s", (digest,))
            return None
        self.execute(
            "UPDATE console_sessions SET last_seen_at = %s WHERE digest = %s", (now, digest)
        )
        return str(row["username"])

    def logout(self, token: str) -> None:
        self.execute("DELETE FROM console_sessions WHERE digest = %s", (digest_of(token),))
