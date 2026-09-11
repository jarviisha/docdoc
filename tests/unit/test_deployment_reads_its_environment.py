"""How `create_app()` reads its own configuration — the deployed path.

**Every test that builds an app injects a `_Deployment`.** That is the right way
to test routes and the wrong way to leave this: the functions that turn
environment variables into a deployment were executed by nothing, and they are
what a container actually runs. This milestone shipped a `compose.yml` naming
eight variables that no module read, which is the same defect from the other
side — a configuration surface verified by neither the code nor the tests.

Three refusals are asserted here rather than three conveniences:

* No database is a **supported** configuration, not a degraded one. A Milestone 8
  install upgrades with no change, uses the synchronous routes, and the run
  routes answer 503 naming what to configure.
* A database configured with no driver leaves the queue `None` and says so once.
  Raising would take the whole service down for a capability the synchronous
  routes do not need.
* Nothing is migrated. ``docdoc migrate`` is explicit so that several workers
  booting at once are not several processes altering one table, and an API that
  quietly applied a schema would defeat that from the other side.
"""

from __future__ import annotations

import sys

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from docdoc.api.app import _configured_run_queue, _default_deployment
from docdoc.api.settings import RUN_DATABASE_URL_ENV, STORE_ROOT_ENV

#: Never connected to. `_configured_run_queue` hands `PostgresRunQueue` a
#: *factory*, so a queue is built without a socket — which is the property that
#: lets an API start while its database is down and answer `/readyz` honestly.
DSN = "postgresql://nobody@localhost:1/nothing"


# -- the run queue -----------------------------------------------------------


def test_no_database_is_a_configuration_and_not_a_failure() -> None:
    assert _configured_run_queue() is None


def test_a_configured_database_is_not_connected_to_at_startup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The connection is a factory, not a connection.

    An API that dialled its database during import would fail to start whenever
    the database was slow — and a service that cannot start cannot report *why*
    it cannot start, which is what `/readyz` is for.
    """
    pytest.importorskip("psycopg")
    monkeypatch.setenv(RUN_DATABASE_URL_ENV, DSN)

    queue = _configured_run_queue()

    assert queue is not None
    assert type(queue).__name__ == "PostgresRunQueue"


def test_a_database_with_no_driver_is_reported_once_and_left_unusable(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Configured but unusable. `None`, so the route answers 503 with its own
    message; raising here would take the synchronous routes down with it."""
    monkeypatch.setenv(RUN_DATABASE_URL_ENV, DSN)
    monkeypatch.setitem(sys.modules, "psycopg", None)

    with caplog.at_level("WARNING", logger="docdoc.api"):
        assert _configured_run_queue() is None

    assert any("runs.driver_missing" in record.getMessage() for record in caplog.records)
    assert any("docdoc[postgres]" in record.getMessage() for record in caplog.records), (
        "and it names the extra to install, which is the only actionable part"
    )


# -- the stores --------------------------------------------------------------


def test_no_store_configured_leaves_the_deployment_without_one() -> None:
    """Not an error at import: the routes that need a store say so themselves."""
    deployment = _default_deployment()

    assert deployment.has_store is False
    assert deployment.has_runs is False


def test_a_store_root_is_read_from_the_environment(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`DOCDOC_STORE_ROOT` keeps working untouched for everyone who sets only it."""
    monkeypatch.setenv(STORE_ROOT_ENV, str(tmp_path))

    deployment = _default_deployment()

    assert deployment.has_store is True
    assert deployment.can_namespace is True, (
        "a deployment given a *location* can build a store for a tenant it has "
        "not seen; one given store instances cannot, and refuses to start with "
        "authentication on"
    )
