"""The set of schemas a running system knows.

Keyed by identity, holding multiple majors of one name at once, and resolving
**only** concrete versions. There is no ``latest``: a request whose meaning
changes when the registry changes is not reproducible, and Principle VIII does
not allow it (FR-014). An outer edge may offer such a convenience, but it
resolves to a concrete version before extracting and records what it resolved to.

Registration is all-or-nothing. A schema that fails any check leaves the registry
exactly as it was (EXT-13) -- there is no half-registered schema to discover
later.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

from docdoc.extraction.errors import SchemaError
from docdoc.extraction.identity import prompt_hash_for, schema_hash_for
from docdoc.extraction.loader import (
    PromptTemplate,
    discover,
    load_prompt,
    load_schema,
    prompt_path_for,
)
from docdoc.extraction.schema import Cardinality, FieldSpec, Schema, parse_identity

__all__ = ["RegisteredSchema", "SchemaDescription", "SchemaRegistry", "default_registry"]


class RegisteredSchema:
    """A schema, its prompt, and the two hashes, resolved once at registration."""

    __slots__ = ("prompt", "prompt_hash", "schema", "schema_hash")

    def __init__(self, *, schema: Schema, prompt: PromptTemplate) -> None:
        self.schema = schema
        self.prompt = prompt
        self.schema_hash = schema_hash_for(schema)
        self.prompt_hash = prompt_hash_for(prompt.text)

    @property
    def identity(self) -> str:
        return self.schema.identity


class SchemaDescription:
    """What a caller can read without running an extraction (FR-018)."""

    __slots__ = ("fields", "identity", "schema_hash")

    def __init__(
        self,
        *,
        identity: str,
        schema_hash: str,
        fields: tuple[tuple[str, str, str, bool, str], ...],
    ) -> None:
        self.identity = identity
        self.schema_hash = schema_hash
        #: ``(path, type, cardinality, required, description)`` per declared field.
        self.fields = fields

    @property
    def field_names(self) -> tuple[str, ...]:
        return tuple(path for path, *_ in self.fields)


class SchemaRegistry:
    """Concrete versions only; concurrent majors; no shadowing."""

    def __init__(self) -> None:
        self._entries: dict[str, RegisteredSchema] = {}

    @classmethod
    def from_paths(cls, paths: Sequence[Path | str]) -> SchemaRegistry:
        """Load every schema directly under each path, with its prompt.

        Which paths is configuration's decision, not the library's -- that is
        what makes adding a document type a data change (FR-049).
        """
        registry = cls()
        for raw in paths:
            root = Path(raw)
            for schema_path in discover(root):
                schema = load_schema(schema_path)
                prompt = load_prompt(schema, prompt_path_for(schema_path))
                registry.register(schema, prompt)
        return registry

    def register(self, schema: Schema, prompt: PromptTemplate) -> RegisteredSchema:
        if schema.identity in self._entries:
            raise SchemaError(
                f"schema {schema.identity!r} is already registered. "
                "An identity is registered once; publish a new major instead",
                identity=schema.identity,
            )
        if prompt.identity != schema.identity:
            raise SchemaError(
                f"prompt is keyed to {prompt.identity!r} but the schema is {schema.identity!r}",
                identity=schema.identity,
            )
        entry = RegisteredSchema(schema=schema, prompt=prompt)
        self._entries[schema.identity] = entry
        return entry

    def resolve(self, identity: str) -> RegisteredSchema:
        """Look up a concrete ``name@version``.

        Raises rather than substituting a neighbouring version, and names what
        *does* exist, because an error that says only "not found" sends the
        caller to read the registry by hand (FR-016, EXT-12).
        """
        name, _version = parse_identity(identity)
        entry = self._entries.get(identity)
        if entry is not None:
            return entry

        same_name = tuple(sorted(k for k in self._entries if k.startswith(f"{name}@")))
        if same_name:
            raise SchemaError(
                f"schema {identity!r} is not registered. Registered versions of {name!r}: "
                f"{', '.join(same_name)}. No neighbouring version is substituted",
                identity=identity,
                available=same_name,
            )
        raise SchemaError(
            f"no schema named {name!r} is registered. Registered identities: "
            f"{', '.join(self.identities()) or '(none)'}",
            identity=identity,
            available=self.identities(),
        )

    def identities(self) -> tuple[str, ...]:
        return tuple(sorted(self._entries))

    def describe(self, identity: str) -> SchemaDescription:
        entry = self.resolve(identity)
        return SchemaDescription(
            identity=entry.identity,
            schema_hash=entry.schema_hash,
            fields=tuple(_describe_fields(entry.schema.fields, prefix="")),
        )

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, identity: object) -> bool:
        return identity in self._entries


def _describe_fields(
    fields: tuple[FieldSpec, ...], *, prefix: str
) -> list[tuple[str, str, str, bool, str]]:
    rows: list[tuple[str, str, str, bool, str]] = []
    for field in fields:
        path = f"{prefix}{field.name}"
        rows.append(
            (
                path,
                "" if field.type is None else str(field.type),
                str(field.cardinality),
                field.required,
                field.description,
            )
        )
        if field.cardinality in (Cardinality.GROUP, Cardinality.REPEATING_GROUP):
            rows.extend(_describe_fields(field.fields, prefix=f"{path}."))
    return rows


def default_registry(paths: Sequence[Path | str] | None = None) -> SchemaRegistry:
    """The registry an application gets when it has not built one itself.

    Symmetric with the ingest layer's own default registry, so a reader who knows
    one knows the other. It reads the paths configuration names and nothing more
    -- there is no bundled schema, because a schema is a deployment's data and
    not docdoc's.
    """
    return SchemaRegistry.from_paths(paths or ())
