"""`create_version`'s race on a new name, and `merged_registry` with the database down."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from docdoc.extraction.registry import SchemaRegistry
from docdoc.runs.console_schemas import (
    ConsoleSchema,
    SchemaNameTaken,
    create_version,
    merged_registry,
)
from docdoc.runs.errors import RunStateUnavailableError
from tests.fixtures.schema_store import InMemorySchemaStore

FILES = SchemaRegistry.from_paths([Path("schemas")])
DOC = {"name": "fresh", "fields": [{"name": "a", "type": "string"}]}


class RacingStore(InMemorySchemaStore):
    """Another operator saves `fresh@1` between our name check and our insert."""

    def insert(self, row: ConsoleSchema) -> bool:
        super().insert(
            ConsoleSchema("fresh", 1, {**DOC, "version": 1}, "p", None, row.created_at, "bob")
        )
        return super().insert(row)


def test_a_lost_race_on_a_new_name_is_name_taken() -> None:
    store = RacingStore()
    with pytest.raises(SchemaNameTaken):
        create_version(
            store,
            FILES,
            document=DOC,
            prompt="Extract.",
            base=None,
            created_by="a",
            now=datetime.now(UTC),
        )
    assert [r.identity for r in store.all()] == ["fresh@1"]


def test_merged_registry_falls_back_to_files_when_the_database_is_down(caplog) -> None:
    class Down:
        def all(self):
            raise RunStateUnavailableError("down")

    assert merged_registry(FILES, Down()) is FILES  # type: ignore[arg-type]
    assert "down" in caplog.text
