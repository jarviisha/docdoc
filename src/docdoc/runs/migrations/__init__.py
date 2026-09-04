"""Numbered SQL files, applied by an explicit step.

**Never at process start** (FR-078). With several workers booting at once, an
implicit migration is several processes racing to alter one table, and the one
that loses reports an error an operator has to learn to ignore. Worse, it makes
a deployment's schema a function of which container happened to start first.

**No Alembic**, and no migration framework at all. Alembic brings SQLAlchemy,
which would become the largest dependency in this project, arriving to version
one table. The repository has a precedent for exactly this trade: the CLI is
`argparse` because, as `pyproject.toml` puts it, it "costs no dependency"
(research R7).

The rules a file must follow, because there is no framework to enforce them:

* named ``NNNN_name.sql``, applied in lexical order, never renumbered;
* idempotent on its own (``IF NOT EXISTS``), so a half-applied migration can be
  re-run rather than reasoned about;
* never edited once applied anywhere — add a new file instead. Nothing here
  hashes file contents to catch that, which is a real gap and a smaller one than
  a checksum table that fires on a whitespace change.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "APPLIED_TABLE",
    "DEFAULT_TENANT_TABLE",
    "Migration",
    "apply",
    "assign_default_tenant",
    "discover",
    "pending",
    "recorded_default_tenant",
]

_HERE = Path(__file__).parent

#: Where applied versions are recorded. Created by `apply` before anything else,
#: so the first run on an empty database is not a special case.
APPLIED_TABLE = "docdoc_schema_version"

_CREATE_APPLIED = f"""
CREATE TABLE IF NOT EXISTS {APPLIED_TABLE} (
    version    text        PRIMARY KEY,
    applied_at timestamptz NOT NULL
)
"""


class Migration:
    """One numbered file."""

    __slots__ = ("path", "version")

    def __init__(self, path: Path) -> None:
        self.path = path
        self.version = path.stem

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    def __repr__(self) -> str:  # pragma: no cover - diagnostics only
        return f"Migration({self.version!r})"


def discover() -> list[Migration]:
    """Every migration shipped with this package, in application order."""
    found = [Migration(path) for path in sorted(_HERE.glob("*.sql"))]
    if not found:
        # A wheel that dropped the .sql files would otherwise report "nothing to
        # do" and leave a deployment with no table and no error.
        raise RuntimeError(
            f"no migrations found in {_HERE}; the package was built without its "
            "SQL files, and `docdoc migrate` would silently do nothing"
        )
    return found


def _applied(connection: object) -> set[str]:
    cursor = connection.execute(f"SELECT version FROM {APPLIED_TABLE}")  # type: ignore[attr-defined]
    return {row[0] for row in cursor.fetchall()}


def pending(connection: object) -> list[Migration]:
    """Migrations not yet recorded as applied.

    Creates the bookkeeping table if it is absent, because "have any been
    applied" is unanswerable otherwise and an empty database is the normal first
    case rather than an error.
    """
    connection.execute(_CREATE_APPLIED)  # type: ignore[attr-defined]
    done = _applied(connection)
    return [m for m in discover() if m.version not in done]


#: Where the store-root owner is recorded. Named here so `docdoc migrate` and the
#: tests refer to one string rather than three.
DEFAULT_TENANT_TABLE = "docdoc_default_tenant"


def recorded_default_tenant(connection: object) -> str | None:
    """Which tenant this database says owns the unprefixed store root.

    ``None`` before the assignment has been made — which includes every
    deployment that has run ``0001`` and not ``0002``, so callers must treat it
    as "not yet answered" rather than as "default".
    """
    cursor = connection.execute(  # type: ignore[attr-defined]
        f"SELECT tenant_id FROM {DEFAULT_TENANT_TABLE} WHERE singleton"
    )
    row = cursor.fetchone()
    return None if row is None else str(row[0])


def assign_default_tenant(connection: object, tenant_id: str, *, now: object) -> str:
    """Record who owns content written before tenants existed (FR-089).

    Explicit, idempotent, and **refuses to change its mind**. Running it twice
    with the same value is a no-op; running it with a different one raises,
    naming both.

    That refusal is the requirement's real content. The recorded value decides
    where every read looks, so moving it after content exists strands that
    content — and the symptom is not an error but *correct answers plus a silent
    re-payment for every parse*, because a miss is indistinguishable from an
    absence. Raising here turns a misconfigured deployment into a failure at
    deploy time instead of a discovery on next month's invoice.

    Returns the value that is in force, which is the existing one on a repeat.
    """
    from docdoc.runs.errors import TenantAssignmentError

    existing = recorded_default_tenant(connection)
    if existing is not None:
        if existing != tenant_id:
            raise TenantAssignmentError(existing, tenant_id)
        return existing

    connection.execute(  # type: ignore[attr-defined]
        f"INSERT INTO {DEFAULT_TENANT_TABLE} (singleton, tenant_id, assigned_at) "
        f"VALUES (true, %s, %s) ON CONFLICT (singleton) DO NOTHING",
        (tenant_id, now),
    )
    # Re-read rather than return the argument: two `docdoc migrate` invocations
    # racing would both see no row and both insert, and the loser must be told
    # what actually landed rather than what it asked for.
    landed = recorded_default_tenant(connection)
    return tenant_id if landed is None else landed


def apply(connection: object, *, now: object) -> Sequence[str]:
    """Apply every pending migration. Returns the versions applied.

    Each migration runs in its own transaction with its bookkeeping row, so a
    failure half way through a set leaves the ones before it applied and
    recorded. Re-running then resumes rather than starting over — which is what
    makes the operation safe to retry without anybody reasoning about where it
    stopped.

    **That holds only on an autocommit connection, and the caller must supply
    one.** On a connection in the default mode, ``pending()``'s
    ``CREATE TABLE IF NOT EXISTS`` has already opened an implicit transaction by
    the time this runs, so ``connection.transaction()`` emits a SAVEPOINT inside
    it and nothing commits until the caller's ``with`` block ends. A failure in
    the third migration then discards the first two, and the promise above is
    exactly inverted. ``docdoc migrate`` connects with ``autocommit=True`` for
    this reason; the assertion below refuses to run without it rather than
    quietly providing the weaker guarantee.

    ``now`` is passed in rather than read here: this module is not
    ``identity.py``, and FR-072 applies to it like everything else.
    """
    if getattr(connection, "autocommit", True) is False:
        raise RuntimeError(
            "migrations need an autocommit connection, or each one runs in a "
            "savepoint of the caller's transaction and a later failure discards "
            "the earlier successes. Connect with psycopg.connect(dsn, "
            "autocommit=True)"
        )

    applied: list[str] = []
    for migration in pending(connection):
        with connection.transaction():  # type: ignore[attr-defined]
            connection.execute(migration.sql)  # type: ignore[attr-defined]
            connection.execute(  # type: ignore[attr-defined]
                f"INSERT INTO {APPLIED_TABLE} (version, applied_at) VALUES (%s, %s)",
                (migration.version, now),
            )
        applied.append(migration.version)
    return applied
