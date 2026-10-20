"""``docdoc console-user add|passwd|disable|list`` — the only account writer.

ADR-0023 §1: "Accounts exist only through `docdoc console-user
add|passwd|disable|list` — the console itself has no account-management
screen." A console session authenticates an operator, and the one way that
session comes to exist is a row this command wrote first — so there is no
route anywhere under ``/v1/console`` that creates, changes, or disables one.
An operator with database access runs this, on a command line, exactly as
the first administrative credential is minted by ``docdoc credential issue
--admin`` and by nothing else.

**The password never appears in argv, output, or a log.** It is read with
``getpass.getpass()`` twice — matched before anything is written, so a typo
is caught before the account is created or changed — or, for scripting,
with ``--password-stdin``, which reads exactly one line from standard input.
Nothing here echoes it, returns it, or folds it into a message; `list`
reports a username and whether it is disabled, never a hash.
"""

from __future__ import annotations

import getpass
import sys
from typing import TYPE_CHECKING, Any

from docdoc.cli.render import Rendering
from docdoc.runs.console_accounts import PostgresConsoleAccounts
from docdoc.runs.errors import RunStateUnavailableError
from docdoc.runs.identity import new_password_salt
from docdoc.runs.identity import now as clock

if TYPE_CHECKING:
    import argparse

    from docdoc.cli.config import Settings

__all__ = ["run"]


def run(args: argparse.Namespace, settings: Settings) -> Rendering:
    """Dispatch on the action, which argparse has already constrained."""
    dsn = getattr(args, "run_database_url", None) or settings.run_database_url
    if not dsn:
        raise RunStateUnavailableError(
            "no run-state database configured; set DOCDOC_RUN_DATABASE_URL or pass "
            "--run-database-url. Console accounts and sessions live there "
            "unconditionally (ADR-0023 §1) — there is no key file to fall back to"
        )

    accounts = PostgresConsoleAccounts(execute=_execute(dsn))
    action = getattr(args, "action", None)

    if action == "add":
        return _add(args, accounts)
    if action == "passwd":
        return _passwd(args, accounts)
    if action == "disable":
        return _disable(args, accounts)
    if action == "list":
        return _list(accounts)
    raise ValueError("usage: docdoc console-user add|passwd|disable|list")


def _add(args: argparse.Namespace, accounts: PostgresConsoleAccounts) -> Rendering:
    """Create an account, or restore a disabled one with a new password."""
    password = _read_password(args)
    now = clock()
    account = accounts.add(args.username, password, salt=new_password_salt(), now=now)
    # A revived account keeps its original `created_at`; a new one carries `now`.
    restored = account.created_at != now
    return Rendering(
        code=0,
        data={
            "username": account.username,
            "created_at": _iso(account.created_at),
            "restored": restored,
        },
        lines=[
            f"console account {account.username!r} restored with a new password"
            if restored
            else f"console account {account.username!r} created"
        ],
    )


def _passwd(args: argparse.Namespace, accounts: PostgresConsoleAccounts) -> Rendering:
    password = _read_password(args)
    accounts.set_password(args.username, password, salt=new_password_salt(), now=clock())
    return Rendering(
        code=0,
        data={"username": args.username},
        lines=[f"password changed for {args.username!r}; its existing sessions were signed out"],
    )


def _disable(args: argparse.Namespace, accounts: PostgresConsoleAccounts) -> Rendering:
    """Idempotent: disabling a disabled account succeeds and changes nothing.

    An unknown username is an error (`ConsoleAccountError`, non-zero exit),
    so a typo never reads as "already disabled".
    """
    was_active = accounts.disable(args.username, now=clock())
    return Rendering(
        code=0,
        data={"username": args.username, "was_active": was_active},
        lines=[
            f"{args.username!r} disabled; its existing sessions were signed out"
            if was_active
            else f"{args.username!r} already disabled; nothing changed"
        ],
    )


def _list(accounts: PostgresConsoleAccounts) -> Rendering:
    """Usernames and timestamps. **No hash, ever** — see the module docstring."""
    every = accounts.accounts()
    return Rendering(
        code=0,
        data={
            "accounts": [
                {
                    "username": account.username,
                    "disabled": account.disabled_at is not None,
                    "created_at": _iso(account.created_at),
                    "disabled_at": _iso(account.disabled_at),
                }
                for account in every
            ]
        },
        lines=[
            f"{account.username:<20} {'disabled' if account.disabled_at else 'active'}"
            for account in every
        ]
        or ["no console accounts"],
    )


def _read_password(args: argparse.Namespace) -> str:
    """The new password, from stdin for a script or a matched double prompt.

    ``--password-stdin`` reads exactly one line and trims only its line
    ending (``\\n`` or ``\\r\\n``) — a password is whatever the line held,
    including any leading or trailing space somebody meant to type. Without
    it, ``getpass.getpass`` is run twice and the two must agree, which is
    what catches a typo before an account is created or changed rather than
    after, when the only sign of one is a login that no longer works.
    """
    if getattr(args, "password_stdin", False):
        return sys.stdin.readline().rstrip("\r\n")

    first = getpass.getpass("New password: ")
    second = getpass.getpass("Confirm new password: ")
    if first != second:
        raise ValueError("passwords did not match; nothing was changed")
    return first


def _iso(value: object) -> str | None:
    return value.isoformat() if value is not None else None  # type: ignore[attr-defined]


def _execute(dsn: str) -> Any:
    """A one-statement executor over a fresh connection.

    The same shape as ``docdoc.cli.commands.credential._execute``, duplicated
    rather than imported for the reason that one is: this command touches two
    tables and has no reason to construct the queue machinery that claims
    runs.
    """
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError as exc:  # pragma: no cover - guarded by the extra
        raise RunStateUnavailableError(
            "psycopg is not installed; run state needs `pip install docdoc[postgres]`"
        ) from exc

    def execute(sql: str, params: Any = (), *, fetch: str | None = None) -> Any:
        try:
            with (
                psycopg.connect(dsn) as connection,
                connection.cursor(row_factory=dict_row) as cursor,
            ):
                cursor.execute(sql, params)
                if fetch == "one":
                    return cursor.fetchone()
                if fetch == "all":
                    return cursor.fetchall()
                return None
        except Exception as exc:
            raise RunStateUnavailableError(str(type(exc).__name__)) from exc

    return execute
