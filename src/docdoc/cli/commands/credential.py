"""``docdoc credential issue | revoke | list`` — and where the first admin comes from.

**`--admin` exists here and at no URL** (FR-032, ADR-0016 §5). A route that could
mint an administrative credential is a route that needs no credential, which is
the hole this whole feature exists to close. So the first one is created by an
operator with database access, on a command line, once.

The consequence is real and is documented rather than designed around: an
operator can revoke every administrative credential and lock themselves out of
the routes. Recovery is this command. The alternative — a credential that cannot
be revoked — is worse.

**The plaintext is printed once.** Nothing stores it; the table holds
``sha256(key)``. There is no command that shows it again, and `list` deliberately
returns neither the key nor its digest.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docdoc.cli.render import Rendering
from docdoc.runs.errors import RunStateUnavailableError
from docdoc.runs.identity import now as clock
from docdoc.runs.keys import ADMIN_SCOPE, PostgresKeyStore
from docdoc.runs.principal import TENANT_PATTERN

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
            "--run-database-url. Credentials with a lifetime need one — the key "
            "file still authenticates, and a key removed from it takes effect on "
            "restart"
        )

    store = PostgresKeyStore(execute=_execute(dsn))
    action = getattr(args, "action", None)

    if action == "issue":
        return _issue(args, store)
    if action == "revoke":
        return _revoke(args, store)
    if action == "list":
        return _list(args, store)
    raise ValueError("usage: docdoc credential issue|revoke|list")


def _issue(args: argparse.Namespace, store: PostgresKeyStore) -> Rendering:
    tenant_id = args.tenant
    if not TENANT_PATTERN.match(tenant_id):
        raise ValueError(
            f"{tenant_id!r} is not a valid tenant identifier: [a-z0-9_-]{{1,64}}. "
            "Narrow enough that it is always a safe path segment (ADR-0014 §1)"
        )

    scopes = frozenset({ADMIN_SCOPE}) if getattr(args, "admin", False) else frozenset()
    secret, credential = store.issue(
        tenant_id=tenant_id,
        scopes=scopes,
        label=getattr(args, "label", None),
        now=clock(),
    )

    return Rendering(
        code=0,
        data={
            "credential_id": str(credential.credential_id),
            "tenant_id": credential.tenant_id,
            "scopes": sorted(scopes),
            "key": secret,
        },
        lines=[
            secret,
            "",
            "This is the only time this credential is readable. Nothing stores "
            "it; the table holds its SHA-256. There is no command and no route "
            "that will show it again.",
        ],
    )


def _revoke(args: argparse.Namespace, store: PostgresKeyStore) -> Rendering:
    """Idempotent: revoking a revoked credential succeeds and changes nothing."""
    from uuid import UUID

    identity = UUID(args.credential_id)
    was_active = store.revoke(identity, now=clock())

    return Rendering(
        code=0,
        data={"credential_id": str(identity), "was_active": was_active},
        lines=[
            (
                "revoked; every process stops accepting it within its configured "
                "credential cache lifetime, with no restart"
            )
            if was_active
            else "already revoked; nothing changed"
        ],
    )


def _list(args: argparse.Namespace, store: PostgresKeyStore) -> Rendering:
    """Identifiers and timestamps. **No key and no digest** (FR-030)."""
    credentials = store.list_for(args.tenant)
    return Rendering(
        code=0,
        data={
            "credentials": [
                {
                    "credential_id": str(credential.credential_id),
                    "tenant_id": credential.tenant_id,
                    "label": credential.label,
                    "scopes": sorted(credential.scopes),
                    "created_at": _iso(credential.created_at),
                    "last_used_at": _iso(credential.last_used_at),
                    "revoked_at": _iso(credential.revoked_at),
                }
                for credential in credentials
            ]
        },
        lines=[
            f"{credential.credential_id}  {credential.tenant_id:<20} "
            f"{'revoked' if credential.revoked else 'active':<8} "
            f"{','.join(sorted(credential.scopes)) or '-'}"
            for credential in credentials
        ]
        or ["no credentials issued to this tenant"],
    )


def _iso(value: object) -> str | None:
    return value.isoformat() if value is not None else None  # type: ignore[attr-defined]


def _execute(dsn: str) -> Any:
    """A one-statement executor over a fresh connection.

    The same shape `PostgresRunQueue._execute` has, built here rather than
    imported because this command holds no queue — it touches one table and has
    no reason to construct the machinery that claims runs.
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
