"""Loading schemas and prompts from data files (FR-049, FR-050).

Schemas are JSON so that the canonical form the kernel already implements is
literally the same function call (research.md R6). TOML would need its own
canonicalisation convention before it could be hashed; YAML would add a base
dependency, which Principle I forbids.

Every defect is rejected **here**, at load time, naming the file and the defect
-- not at first use, when the caller is somewhere else entirely (FR-019).
"""

from __future__ import annotations

import json
from pathlib import Path  # noqa: TC003 - runtime use in prompt_path_for and discover
from typing import Any

from pydantic import ValidationError

from docdoc.extraction.errors import SchemaError
from docdoc.extraction.schema import Schema

__all__ = ["PromptTemplate", "load_prompt", "load_schema", "prompt_path_for"]

_SCHEMA_SUFFIX = ".json"
_PROMPT_SUFFIX = ".md"


class PromptTemplate:
    """Instruction data keyed to one schema identity.

    Data, never code: a prompt in code is a document-type-specific code path
    wearing a disguise, which Principle VI forbids by name.
    """

    __slots__ = ("identity", "text")

    def __init__(self, *, identity: str, text: str) -> None:
        self.identity = identity
        self.text = text

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"PromptTemplate(identity={self.identity!r}, {len(self.text)} chars)"


def load_schema(path: Path) -> Schema:
    """Parse one schema file, or raise ``SchemaError`` naming the file.

    Nothing is partially constructed: either a valid ``Schema`` comes back or
    the caller gets an error and the registry is untouched (EXT-13).
    """
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(f"schema file could not be read: {exc}", path=str(path)) from exc

    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SchemaError(
            f"schema file is not valid JSON: {exc.msg} at line {exc.lineno} column {exc.colno}",
            path=str(path),
        ) from exc

    if not isinstance(payload, dict):
        raise SchemaError(
            f"schema file must contain a JSON object, got {type(payload).__name__}",
            path=str(path),
        )

    try:
        return Schema.model_validate(_normalise(payload))
    except ValidationError as exc:
        raise SchemaError(_describe(exc), path=str(path)) from exc


def _normalise(payload: dict[str, Any]) -> dict[str, Any]:
    """Accept ``fields`` as a list and leave everything else to the model.

    Deliberately thin. A loader that rewrites its input is a second, undocumented
    schema language.
    """
    return payload


def _describe(exc: ValidationError) -> str:
    """One line per structural defect, each naming the field path.

    A caller reading "1 validation error for Schema" learns nothing; the point of
    rejecting at load time is that the message says which field and why.
    """
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(item) for item in error["loc"]) or "<root>"
        parts.append(f"{location}: {error['msg']}")
    return "invalid schema -- " + "; ".join(parts)


def prompt_path_for(schema_path: Path) -> Path:
    """Where a schema's prompt lives, by convention: ``prompts/<stem>.md``."""
    return schema_path.parent / "prompts" / f"{schema_path.stem}{_PROMPT_SUFFIX}"


def load_prompt(schema: Schema, path: Path) -> PromptTemplate:
    """Load the prompt beside a schema, or raise.

    A schema without a prompt is refused at registration rather than reaching a
    model with no instructions, which would be a silent quality failure instead
    of a loud one.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SchemaError(
            f"schema {schema.identity!r} has no prompt at {path}. "
            "A schema is registered with its instructions or not at all",
            identity=schema.identity,
            path=str(path),
        ) from exc
    if not text.strip():
        raise SchemaError(
            f"prompt for schema {schema.identity!r} is empty",
            identity=schema.identity,
            path=str(path),
        )
    return PromptTemplate(identity=schema.identity, text=text)


def discover(root: Path) -> list[Path]:
    """Every schema file directly under ``root``, in sorted order.

    Sorted so registration order is deterministic across filesystems; the
    registry does not depend on it, and a test that did would be depending on
    ``os.listdir``.
    """
    if not root.is_dir():
        raise SchemaError(f"schema path is not a directory: {root}", path=str(root))
    return sorted(p for p in root.glob(f"*{_SCHEMA_SUFFIX}") if p.is_file())
