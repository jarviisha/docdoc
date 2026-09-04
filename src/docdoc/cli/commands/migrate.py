"""``docdoc migrate [--check]`` — apply the run-state schema, explicitly.

**Never at process start** (FR-078). With several workers booting at once an
implicit migration is several processes racing to alter one table, and the loser
reports an error an operator learns to ignore. It also makes a deployment's
schema depend on which container happened to start first, which is the kind of
thing that works in staging.

``--check`` exits non-zero when anything is pending and applies nothing. That is
the form a deployment pipeline gates on: a rollout that starts workers against a
database missing the table they need should stop before the workers do.

The database is named by ``DOCDOC_RUN_DATABASE_URL`` or ``--run-database-url``,
following the precedence every other setting follows — explicit argument over
environment over default — except that there is no default. Like
``DOCDOC_STORE_ROOT``, where run state accumulates is an operator's decision, and
a command that invented a database to write to would be making it for them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docdoc.artifacts.paths import root_tenant
from docdoc.cli.render import Rendering
from docdoc.runs import migrations
from docdoc.runs.errors import RunError, RunStateUnavailableError
from docdoc.runs.identity import now as clock

if TYPE_CHECKING:
    import argparse

    from docdoc.cli.config import Settings

__all__ = ["EXIT_PENDING", "run"]

#: ``--check`` found work to do. Not an error in the "docdoc is broken" sense —
#: it is a real answer, and it earns its own code for the same reason a document
#: being invalid earns `1` rather than `2`.
EXIT_PENDING = 1


def run(args: argparse.Namespace, settings: Settings) -> Rendering:
    """Apply pending migrations, or report them."""
    dsn = getattr(args, "run_database_url", None) or settings.run_database_url
    if not dsn:
        raise RunStateUnavailableError(
            "no run-state database configured; set DOCDOC_RUN_DATABASE_URL or pass "
            "--run-database-url. There is no default, because where run state "
            "accumulates is your decision"
        )

    try:
        import psycopg
    except ImportError as exc:
        raise RunStateUnavailableError(
            "psycopg is not installed; run state needs `pip install docdoc[postgres]`"
        ) from exc

    try:
        # `autocommit=True` is what makes `migrations.apply`'s promise true.
        # Without it, `pending()`'s `CREATE TABLE IF NOT EXISTS` opens an implicit
        # transaction, so `apply`'s `with connection.transaction()` emits a
        # SAVEPOINT rather than starting one — and nothing commits until this
        # `with` block exits. A failure in the second migration then rolled back
        # the first one *and* its bookkeeping row, leaving the database at
        # version zero while the module's docstring promised "a failure half way
        # through a set leaves the ones before it applied". Re-running could not
        # resume, because there was nothing recorded to resume from.
        with psycopg.connect(dsn, autocommit=True) as connection:
            if getattr(args, "check", False):
                outstanding = [m.version for m in migrations.pending(connection)]
                return Rendering(
                    code=EXIT_PENDING if outstanding else 0,
                    data={"pending": outstanding, "applied": []},
                    lines=(
                        [f"pending: {', '.join(outstanding)}"] if outstanding else ["up to date"]
                    ),
                )

            now = clock()
            applied = list(migrations.apply(connection, now=now))
            # The explicit step FR-089 asks for, run after the schema exists and
            # in the same command. Separate from the SQL because SQL cannot read
            # an environment, and a value hard-coded in a migration file would be
            # the inferred owner the requirement forbids.
            owner = migrations.assign_default_tenant(
                connection,
                root_tenant(getattr(args, "default_tenant", None)),
                now=now,
            )
    except RunError:
        # `assign_default_tenant` refusing to move the store root, or the queue
        # reporting the database unreachable. Both are already typed and both
        # already say what happened; re-wrapping either would replace an
        # explanation with a category.
        raise
    except Exception as exc:
        # Same boundary rule as `PostgresRunQueue._execute`: no driver exception
        # reaches a caller, because its type changes when psycopg releases.
        raise RunStateUnavailableError(str(type(exc).__name__)) from exc

    return Rendering(
        code=0,
        data={"pending": [], "applied": applied, "default_tenant": owner},
        lines=([f"applied: {', '.join(applied)}"] if applied else ["nothing to apply"])
        + [f"store root belongs to tenant: {owner}"],
    )
