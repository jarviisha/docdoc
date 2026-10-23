"""Storage for schemas authored in the console, and their merge over the files.

A row is one immutable identity: it is inserted once, never updated, never
deleted. `merged_registry` is where a row meets the same validation a schema file
gets; a bad row is skipped and logged, never allowed to fail the registry.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from docdoc.extraction.errors import SchemaError
from docdoc.extraction.loader import parse_schema, prompt_from_text

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from docdoc.extraction.registry import SchemaRegistry

__all__ = ["ConsoleSchema", "PostgresSchemaStore", "SchemaStore", "merged_registry"]

_log = logging.getLogger("docdoc.runs")


@dataclass(frozen=True)
class ConsoleSchema:
    name: str
    version: int
    document: dict[str, Any]
    prompt: str
    base: str | None
    created_at: datetime
    created_by: str

    @property
    def identity(self) -> str:
        return f"{self.name}@{self.version}"


class SchemaStore(Protocol):
    def all(self) -> tuple[ConsoleSchema, ...]: ...
    def get(self, identity: str) -> ConsoleSchema | None: ...
    def insert(self, row: ConsoleSchema) -> bool:
        """Store the row; ``False`` when its version is already taken."""
        ...


_COLUMNS = "name, version, document, prompt, base, created_at, created_by"


def _row(r: dict[str, Any]) -> ConsoleSchema:
    doc = r["document"]
    return ConsoleSchema(
        name=r["name"],
        version=r["version"],
        document=json.loads(doc) if isinstance(doc, str | bytes) else doc,
        prompt=r["prompt"],
        base=r["base"],
        created_at=r["created_at"],
        created_by=r["created_by"],
    )


@dataclass
class PostgresSchemaStore:
    """``execute`` is `PostgresRunQueue._execute`, as in the correction store."""

    execute: Callable[..., Any]

    def all(self) -> tuple[ConsoleSchema, ...]:
        rows = self.execute(
            f"SELECT {_COLUMNS} FROM console_schemas ORDER BY name, version", fetch="all"
        )
        return tuple(_row(r) for r in rows or ())

    def get(self, identity: str) -> ConsoleSchema | None:
        name, _, version = identity.rpartition("@")
        if not name or not version.isdecimal():
            return None
        r = self.execute(
            f"SELECT {_COLUMNS} FROM console_schemas WHERE name = %s AND version = %s",
            (name, int(version)),
            fetch="one",
        )
        return _row(r) if r else None

    def insert(self, row: ConsoleSchema) -> bool:
        r = self.execute(
            "INSERT INTO console_schemas (name, version, document, prompt, base, "
            "created_at, created_by) VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING RETURNING name",
            (
                row.name,
                row.version,
                json.dumps(row.document),
                row.prompt,
                row.base,
                row.created_at,
                row.created_by,
            ),
            fetch="one",
        )
        return r is not None


def merged_registry(files: SchemaRegistry, store: SchemaStore) -> SchemaRegistry:
    """The file registry plus every valid console row; console wins a clash."""
    good = []
    for row in store.all():
        try:
            schema = parse_schema(row.document, path=row.identity)
            if schema.identity != row.identity:
                _log.warning(
                    "skipping console schema %s: its document is %s", row.identity, schema.identity
                )
                continue
            good.append((schema, prompt_from_text(schema, row.prompt, path=row.identity)))
        except SchemaError as exc:
            _log.warning("skipping console schema %s: %s", row.identity, exc)
    merged = files.with_console(good)
    for identity in merged.shadowed[len(files.shadowed) :]:
        _log.warning("console schema %s replaces the file of the same identity", identity)
    return merged
