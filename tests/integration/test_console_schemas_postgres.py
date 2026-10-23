"""The console schema store against a real database."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.infra import require_database

from docdoc.extraction.registry import SchemaRegistry
from docdoc.runs import migrations
from docdoc.runs.console_schemas import ConsoleSchema, PostgresSchemaStore, merged_registry
from docdoc.runs.postgres import PostgresRunQueue

pytestmark = pytest.mark.postgres

SCHEMAS = Path(__file__).parents[2] / "schemas"


@pytest.fixture
def store() -> PostgresSchemaStore:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE console_schemas")
    return PostgresSchemaStore(execute=PostgresRunQueue(lambda: psycopg.connect(dsn))._execute)


def row(name: str, version: int, document: Any = None) -> ConsoleSchema:
    if document is None:
        document = json.loads((SCHEMAS / "invoice@1.json").read_text())
        document.update(name=name, version=version)
    return ConsoleSchema(
        name=name,
        version=version,
        document=document,
        prompt="Extract the fields.",
        base=None,
        created_at=datetime(2026, 10, 23, tzinfo=UTC),
        created_by="ana",
    )


def test_insert_then_get_and_all(store):
    assert store.insert(row("invoice", 3))
    assert store.get("invoice@3").document["name"] == "invoice"
    assert [r.identity for r in store.all()] == ["invoice@3"]
    assert store.get("invoice@4") is None


def test_insert_refuses_a_taken_version(store):
    assert store.insert(row("invoice", 3))
    assert store.insert(row("invoice", 3)) is False


def test_merged_registry_skips_an_invalid_row(store, caplog):
    store.insert(row("bad", 1, document={"name": "bad", "version": 1, "fields": "nope"}))
    with caplog.at_level(logging.WARNING, logger="docdoc.runs"):
        merged = merged_registry(SchemaRegistry.from_paths([SCHEMAS]), store)
    assert "bad@1" not in merged
    assert "invoice@1" in merged
    assert "bad@1" in caplog.text


def test_merged_registry_logs_a_shadowed_file(store, caplog):
    store.insert(row("invoice", 1))
    with caplog.at_level(logging.WARNING, logger="docdoc.runs"):
        merged = merged_registry(SchemaRegistry.from_paths([SCHEMAS]), store)
    assert merged.shadowed == ("invoice@1",)
    assert "invoice@1" in caplog.text


def test_merged_registry_skips_a_row_whose_document_names_another_identity(store, caplog):
    store.insert(row("a", 1, document=row("b", 1).document))
    store.insert(row("b", 1))
    with caplog.at_level(logging.WARNING, logger="docdoc.runs"):
        merged = merged_registry(SchemaRegistry.from_paths([SCHEMAS]), store)
    assert "a@1" not in merged
    assert "b@1" in merged
    assert "a@1" in caplog.text
