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
from docdoc.runs.errors import RunStateUnavailableError

if TYPE_CHECKING:
    from collections.abc import Callable
    from datetime import datetime

    from docdoc.extraction.registry import SchemaRegistry

__all__ = [
    "ConsoleSchema",
    "PostgresSchemaStore",
    "SchemaNameTaken",
    "SchemaStore",
    "SchemaVersionTaken",
    "create_version",
    "merged_registry",
]

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


def merged_registry(
    files: SchemaRegistry, store: SchemaStore, *, strict: bool = False
) -> SchemaRegistry:
    """The file registry plus every valid console row; console wins a clash.

    When the database is unreachable the files alone are returned, unless
    ``strict``: a worker must wait and retry rather than run a file's content
    under an identity a console row shadows.
    """
    try:
        rows = store.all()
    except RunStateUnavailableError as exc:
        if strict:
            raise
        _log.warning("console schemas unavailable, serving the files only: %s", exc)
        return files
    good = []
    for row in rows:
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


class SchemaNameTaken(Exception):  # noqa: N818 - a refusal, not a fault
    """A new schema (no base) names something that already exists."""


class SchemaVersionTaken(Exception):  # noqa: N818 - a refusal, not a fault
    """Every attempt to claim the next version lost a race."""


_ATTEMPTS = 3


def create_version(
    store: SchemaStore,
    registry: SchemaRegistry,
    *,
    document: dict[str, Any],
    prompt: str,
    base: str | None,
    created_by: str,
    now: datetime,
) -> ConsoleSchema:
    """Validate and store a new version; the server picks the number.

    ``registry`` is the merged one, so it already holds the console rows it was
    built from; the store is read again each attempt for rows added since.
    """
    name = document.get("name")
    if not isinstance(name, str):
        raise SchemaError("the schema document needs a string name")
    if base is None:
        if any(e.schema.name == name for e in registry.entries()) or any(
            r.name == name for r in store.all()
        ):
            raise SchemaNameTaken(
                f"a schema named {name!r} already exists; open it and save a new version instead"
            )
    elif registry.resolve(base).schema.name != name:  # raises SchemaError when unknown
        raise SchemaError(f"the document is named {name!r}, not the name of {base!r}")
    for _ in range(_ATTEMPTS if base is not None else 1):
        taken = [e.schema.version for e in registry.entries() if e.schema.name == name]
        taken += [r.version for r in store.all() if r.name == name]
        doc = {**document, "version": max(taken, default=0) + 1}
        schema = parse_schema(doc)
        prompt_from_text(schema, prompt)
        row = ConsoleSchema(
            name=name,
            version=schema.version,
            document=doc,
            prompt=prompt,
            base=base,
            created_at=now,
            created_by=created_by,
        )
        if store.insert(row):
            return row
    if base is None:  # lost a race on a brand-new name; never fall through to name@2
        raise SchemaNameTaken(
            f"a schema named {name!r} already exists; open it and save a new version instead"
        )
    raise SchemaVersionTaken(
        f"could not assign a version for {name!r} after {_ATTEMPTS} attempts; try again"
    )
