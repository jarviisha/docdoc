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

__all__ = ["APPLIED_TABLE", "Migration", "apply", "discover", "pending"]

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


def apply(connection: object, *, now: object) -> Sequence[str]:
    """Apply every pending migration. Returns the versions applied.

    Each migration runs in its own transaction with its bookkeeping row, so a
    failure half way through a set leaves the ones before it applied and
    recorded. Re-running then resumes rather than starting over — which is what
    makes the operation safe to retry without anybody reasoning about where it
    stopped.

    ``now`` is passed in rather than read here: this module is not
    ``identity.py``, and FR-072 applies to it like everything else.
    """
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
