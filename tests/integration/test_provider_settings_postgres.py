"""The provider settings store against a real database."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from tests.fixtures.provider_store import InMemoryProviderStore
from tests.infra import require_database

from docdoc.runs import migrations
from docdoc.runs.postgres import PostgresRunQueue
from docdoc.runs.provider_settings import (
    DEFAULT_SCOPE,
    KEEP_KEY,
    PostgresProviderStore,
    ScopeSettings,
)

pytestmark = pytest.mark.postgres

NOW = datetime(2026, 10, 23, tzinfo=UTC)
TABLES = "provider_priorities, provider_settings"


@pytest.fixture
def pg():
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute(f"TRUNCATE {TABLES}")
    queue = PostgresRunQueue(lambda: psycopg.connect(dsn))
    yield queue
    # Rows left behind would show up in every other test that reads the shared database.
    with psycopg.connect(dsn, autocommit=True) as connection:
        connection.execute(f"TRUNCATE {TABLES}")


@pytest.fixture(params=["postgres", "memory"])
def store(request, pg):
    if request.param == "memory":
        return InMemoryProviderStore()
    return PostgresProviderStore(execute=pg._execute)


def test_empty_scope(store):
    assert store.scope("acme") == ScopeSettings(None, {})


def test_set_and_read_priority(store):
    store.set_priority("acme", ("b", "a"), by="ana", now=NOW)
    store.set_priority("acme", ("a", "b"), by="ana", now=NOW)
    assert store.scope("acme").priority == ("a", "b")
    assert store.scope(DEFAULT_SCOPE).priority is None


def test_priority_none_deletes(store):
    store.set_priority("acme", ("a",), by="ana", now=NOW)
    store.set_priority("acme", None, by="ana", now=NOW)
    assert store.scope("acme").priority is None


def test_set_model_keeps_key(store):
    store.set_adapter("acme", "gemini", model="m1", key=(b"\x00sealed", "1234"), by="ana", now=NOW)
    store.set_adapter("acme", "gemini", model="m2", key=KEEP_KEY, by="bo", now=NOW)
    got = store.scope("acme").adapters["gemini"]
    assert (got.model, got.key_ciphertext, got.key_last4, got.updated_by) == (
        "m2",
        b"\x00sealed",
        "1234",
        "bo",
    )
    assert type(got.key_ciphertext) is bytes


def test_clear_key(store):
    store.set_adapter("acme", "gemini", model="m1", key=(b"sealed", "1234"), by="ana", now=NOW)
    store.set_adapter("acme", "gemini", model="m1", key=None, by="ana", now=NOW)
    got = store.scope("acme").adapters["gemini"]
    assert (got.model, got.key_ciphertext, got.key_last4) == ("m1", None, None)


def test_all_null_deletes_row(store):
    store.set_adapter("acme", "gemini", model="m1", key=(b"sealed", "1234"), by="ana", now=NOW)
    store.set_adapter("acme", "gemini", model=None, key=None, by="ana", now=NOW)
    assert store.scope("acme").adapters == {}
    # Keeping the key with no model keeps the row; with no key either it goes.
    store.set_adapter("acme", "azure", model=None, key=KEEP_KEY, by="ana", now=NOW)
    assert store.scope("acme").adapters == {}


def test_reset_tenant(store):
    for scope in ("acme", DEFAULT_SCOPE):
        store.set_priority(scope, ("a",), by="ana", now=NOW)
        store.set_adapter(scope, "a", model="m", key=None, by="ana", now=NOW)
    store.reset("acme")
    assert store.scope("acme") == ScopeSettings(None, {})
    assert store.scope(DEFAULT_SCOPE).priority == ("a",)


def test_reset_default_refused(store):
    with pytest.raises(ValueError, match="default"):
        store.reset(DEFAULT_SCOPE)


def test_erase_purges_provider_rows(pg):
    store = PostgresProviderStore(execute=pg._execute)
    store.set_priority("acme", ("a",), by="ana", now=NOW)
    store.set_adapter("acme", "a", model="m", key=None, by="ana", now=NOW)
    counts = pg.purge_tenant_rows("acme")
    assert (counts["provider_priorities"], counts["provider_settings"]) == (1, 1)
    assert store.scope("acme") == ScopeSettings(None, {})
