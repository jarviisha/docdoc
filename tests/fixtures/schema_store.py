"""An in-memory `SchemaStore`, with the Postgres store's insert-once semantics."""

from __future__ import annotations

from docdoc.runs.console_schemas import ConsoleSchema


class InMemorySchemaStore:
    def __init__(self) -> None:
        self._rows: dict[str, ConsoleSchema] = {}

    def all(self) -> tuple[ConsoleSchema, ...]:
        return tuple(sorted(self._rows.values(), key=lambda r: (r.name, r.version)))

    def get(self, identity: str) -> ConsoleSchema | None:
        return self._rows.get(identity)

    def insert(self, row: ConsoleSchema) -> bool:
        if row.identity in self._rows:
            return False
        self._rows[row.identity] = row
        return True
