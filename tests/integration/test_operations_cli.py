"""``docdoc sweep``, ``docdoc erase`` and ``docdoc credential``, run as typed.

**All three were at zero coverage.** They are the operator half of this
milestone — the retention sweep a deployment with no worker would otherwise
never run, the erasure that answers a deletion request, and the only way an
administrative credential comes into existence (FR-032) — and not one line of
any of them was executed by the test suite. The stores beneath them are well
covered; the commands that dispatch to them were not, so an argument named
wrongly, a report field renamed, or an exit code inverted would have shipped.

Driven through ``main(argv)`` rather than by calling ``run(args, settings)``,
because the thing an operator types is the argument vector: a test that built
the `Namespace` itself would pass while ``--purge-store-root`` was spelled
``--purge-root`` in the parser.

`postgres`-marked because all three need run state. That is not a testing
convenience — `docdoc credential` refuses to start without a database and says
so, which is the first thing asserted below.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from tests.infra import require_database

from docdoc.runs import migrations
from docdoc.runs.identity import new_run_id
from docdoc.runs.model import RunOutcome, RunStatus
from docdoc.runs.postgres import PostgresRunQueue

pytestmark = pytest.mark.postgres

TENANT = "acme"
BLOB_ID = "sha256:" + "a" * 64
PROCESSING_ID = "sha256:" + "b" * 64


@pytest.fixture
def dsn() -> str:
    psycopg = pytest.importorskip("psycopg")
    url = require_database()
    with psycopg.connect(url, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute(
            "TRUNCATE runs, corrections, run_tombstones, deliveries, callbacks, credentials"
        )
    return url


@pytest.fixture
def queue(dsn: str) -> PostgresRunQueue:
    import psycopg

    return PostgresRunQueue(lambda: psycopg.connect(dsn))


def run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict]:
    """One invocation, its exit code, and the JSON document it wrote.

    ``--json`` on every call: the human rendering is a separate concern and
    asserting on prose would make these tests fail on a reworded sentence.
    """
    from docdoc.cli import main

    code = main([*argv, "--json"])
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else {}


class _Expired:
    """A run submitted already past its deadline, so a sweep finds it."""

    tenant_id = TENANT
    blob_id = BLOB_ID
    schema_identity = "invoice@1"
    request_id = None
    idempotency_key = None
    priority = 0
    callback_id = None


def _expired_run(queue: PostgresRunQueue):
    at = datetime.now(UTC) - timedelta(days=400)
    run_ = queue.submit(
        _Expired(),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=at,
        expires_at=at + timedelta(days=1),
    )
    queue.finish(
        run_.run_id,
        RunOutcome(status=RunStatus.SUCCEEDED, processing_id=PROCESSING_ID, stage_outcomes=()),
        now=at,
    )
    return run_


# -- docdoc sweep ------------------------------------------------------------


def test_sweep_with_no_retention_period_says_so_rather_than_sweeping(
    dsn: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """`EXIT_NOT_CONFIGURED`, not a traceback and not a silent zero.

    An operator who never opted into retention should be told that. A `0` here
    would read as "swept, nothing to sweep", which is a different fact.
    """
    from docdoc.cli.commands.sweep import EXIT_NOT_CONFIGURED

    code, body = run(capsys, "sweep", "--run-database-url", dsn, "--store", str(tmp_path))

    assert code == EXIT_NOT_CONFIGURED
    assert body == {"swept": False, "reason": "no_retention_period"}


def test_sweep_removes_an_expired_run(
    dsn: str, queue: PostgresRunQueue, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-117's other half: the sweep an operator runs by hand."""
    expired = _expired_run(queue)

    code, body = run(
        capsys,
        "sweep",
        "--retention-days",
        "30",
        "--run-database-url",
        dsn,
        "--store",
        str(tmp_path),
    )

    assert code == 0
    assert body["runs"] == 1
    assert body["degraded"] is False
    assert queue.get(expired.run_id, TENANT) is None


def test_sweep_all_keeps_going_until_a_pass_finds_nothing(
    dsn: str, queue: PostgresRunQueue, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-015: bounded per pass, not per invocation.

    Three expired runs and a batch of one — a single pass would remove one and
    report success, which is the failure mode `--all` exists for.
    """
    for _ in range(3):
        _expired_run(queue)

    code, body = run(
        capsys,
        "sweep",
        "--retention-days",
        "30",
        "--batch",
        "1",
        "--all",
        "--run-database-url",
        dsn,
        "--store",
        str(tmp_path),
    )

    assert code == 0
    assert body["runs"] == 3


# -- docdoc erase ------------------------------------------------------------


def test_erase_removes_a_named_tenants_runs(
    dsn: str, queue: PostgresRunQueue, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A named tenant goes by prefix: one operation, not a scan."""
    erased = _expired_run(queue)

    code, body = run(
        capsys, "erase", "--tenant", TENANT, "--run-database-url", dsn, "--store", str(tmp_path)
    )

    assert code == 0
    assert body["runs"] == 1
    assert queue.get(erased.run_id, TENANT) is None


def test_erasing_one_document_leaves_the_tenants_other_runs(
    dsn: str, queue: PostgresRunQueue, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--document`` is the narrow request, and it must stay narrow."""
    targeted = _expired_run(queue)

    class _Other(_Expired):
        blob_id = "sha256:" + "d" * 64

    at = datetime.now(UTC)
    survivor = queue.submit(
        _Other(),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=at,
        expires_at=at + timedelta(days=30),
    )

    code, body = run(
        capsys,
        "erase",
        "--tenant",
        TENANT,
        "--document",
        BLOB_ID,
        "--run-database-url",
        dsn,
        "--store",
        str(tmp_path),
    )

    assert code == 0
    assert body["runs"] == 1
    assert queue.get(targeted.run_id, TENANT) is None
    assert queue.get(survivor.run_id, TENANT) is not None


def test_erasing_the_default_tenant_explains_what_it_did_not_remove(
    dsn: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0014 §3, and the sentence is the point.

    The default tenant's namespace *is* the store root, so this removes only
    run-derived content. Said every time rather than only when something
    survives, because the operator's mental model is what it corrects.
    """
    from docdoc.artifacts.paths import root_tenant
    from docdoc.cli import main

    code = main(
        ["erase", "--tenant", root_tenant(), "--run-database-url", dsn, "--store", str(tmp_path)]
    )
    out = capsys.readouterr().out

    assert code == 0
    assert "--purge-store-root" in out
    assert "run-derived" in out


# -- docdoc credential -------------------------------------------------------


def test_a_credential_is_readable_once_and_never_again(
    dsn: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-030. The table holds a SHA-256; the key itself exists in that response
    and nowhere else."""
    code, issued = run(
        capsys,
        "credential",
        "issue",
        "--tenant",
        TENANT,
        "--label",
        "operations",
        "--run-database-url",
        dsn,
        "--store",
        str(tmp_path),
    )

    assert code == 0
    key = issued["key"]
    assert key

    _, listed = run(
        capsys,
        "credential",
        "list",
        "--tenant",
        TENANT,
        "--run-database-url",
        dsn,
        "--store",
        str(tmp_path),
    )
    only = listed["credentials"][0]

    assert only["credential_id"] == issued["credential_id"]
    assert only["label"] == "operations"
    assert only["revoked_at"] is None
    # Neither the key nor its digest: a listing that carried either would make
    # every operator who can list credentials able to use them.
    assert key not in json.dumps(listed)
    assert "sha256" not in json.dumps(listed)


def test_the_administrative_scope_comes_from_the_command_line_and_nowhere_else(
    dsn: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """FR-032. ``--admin`` exists here and at no URL."""
    _, plain = run(
        capsys,
        "credential",
        "issue",
        "--tenant",
        TENANT,
        "--run-database-url",
        dsn,
        "--store",
        str(tmp_path),
    )
    _, admin = run(
        capsys,
        "credential",
        "issue",
        "--tenant",
        TENANT,
        "--admin",
        "--run-database-url",
        dsn,
        "--store",
        str(tmp_path),
    )

    assert plain["scopes"] == []
    assert admin["scopes"] == ["admin"]


def test_revoking_is_idempotent_and_says_which_time_it_was(
    dsn: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Revoking a revoked credential succeeds and changes nothing."""
    _, issued = run(
        capsys,
        "credential",
        "issue",
        "--tenant",
        TENANT,
        "--run-database-url",
        dsn,
        "--store",
        str(tmp_path),
    )
    identity = issued["credential_id"]

    first_code, first = run(
        capsys,
        "credential",
        "revoke",
        identity,
        "--run-database-url",
        dsn,
        "--store",
        str(tmp_path),
    )
    second_code, second = run(
        capsys,
        "credential",
        "revoke",
        identity,
        "--run-database-url",
        dsn,
        "--store",
        str(tmp_path),
    )

    assert first_code == second_code == 0
    assert first["was_active"] is True
    assert second["was_active"] is False


def test_an_invalid_tenant_identifier_is_refused_before_anything_is_issued(
    dsn: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    """ADR-0014 §1: narrow enough that a tenant is always a safe path segment."""
    code, _ = run(
        capsys,
        "credential",
        "issue",
        "--tenant",
        "../etc",
        "--run-database-url",
        dsn,
        "--store",
        str(tmp_path),
    )

    assert code != 0

    _, listed = run(
        capsys,
        "credential",
        "list",
        "--tenant",
        TENANT,
        "--run-database-url",
        dsn,
        "--store",
        str(tmp_path),
    )
    assert listed["credentials"] == []


def test_a_tenant_holding_nothing_is_told_so(
    dsn: str, tmp_path, capsys: pytest.CaptureFixture[str]
) -> None:
    from docdoc.cli import main

    code = main(
        [
            "credential",
            "list",
            "--tenant",
            "globex",
            "--run-database-url",
            dsn,
            "--store",
            str(tmp_path),
        ]
    )

    assert code == 0
    assert "no credentials issued" in capsys.readouterr().out


def test_credential_without_a_database_names_the_missing_setting(
    tmp_path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Credentials with a lifetime need one, and the message has to say which.

    Not a `postgres` dependency at all — this is the path an operator hits
    *before* they have configured a database, which is exactly when a traceback
    would be least useful.
    """
    from docdoc.cli import main

    monkeypatch.delenv("DOCDOC_RUN_DATABASE_URL", raising=False)

    code = main(["credential", "list", "--tenant", TENANT, "--store", str(tmp_path)])
    err = capsys.readouterr().err

    assert code != 0
    assert "DOCDOC_RUN_DATABASE_URL" in err
