"""Content-addressed identity (ADR-0002, research.md R3/R4).

Two levels, deliberately distinct:

``blob_id``
    Identifies the **source file**. Derived from the original bytes alone.

``document_id``
    Identifies **one specific parse** of that file. Derived from the blob
    identity together with the identity, version, and options of whatever
    produced the document.

Spans and geometry anchor to ``document_id``, never to ``blob_id`` alone: two
parsers over identical bytes produce incompatible text positions, and a single
shared identity would let one parse's spans be applied silently to the other.

Inputs are hashed as a **canonical JSON object with named fields**, never as
concatenated strings. Concatenation is ambiguous: ``parser_id="pdf"`` with
``version="1.0"`` and ``parser_id="pdf1"`` with ``version=".0"`` would produce
identical input and therefore identical identity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Any

from docdoc.kernel.errors import IdentityError

__all__ = [
    "IDENTITY_SCHEMA_VERSION",
    "ID_PATTERN",
    "blob_id_for",
    "canonical_json",
    "content_id_for",
    "document_id_for",
    "options_hash_for",
]

#: Bumped if the derivation itself ever changes, so old identities stay readable.
IDENTITY_SCHEMA_VERSION = 1

ID_PREFIX = "sha256:"
ID_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")

_JSON_SCALARS = (str, int, float, bool, type(None))


def _validate_json_value(value: Any, path: str) -> None:
    """Reject anything that cannot be canonically encoded.

    Non-finite floats break both JSON interop and hash stability; non-string
    keys have no stable ordering. Both are rejected explicitly rather than
    coerced, so a caller never gets a silently different identity.
    """
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise IdentityError(
                f"non-finite float at {path!r} cannot be canonically encoded",
                field=path,
                detail=f"got {value!r}",
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise IdentityError(
                    f"non-string key at {path!r} has no stable ordering",
                    field=path,
                    detail=f"got key of type {type(key).__name__}",
                )
            _validate_json_value(item, f"{path}.{key}" if path else key)
        return
    if isinstance(value, (list, tuple)):
        for index, item in enumerate(value):
            _validate_json_value(item, f"{path}[{index}]")
        return
    raise IdentityError(
        f"value at {path!r} is not a JSON primitive",
        field=path,
        detail=f"got {type(value).__name__}",
    )


def canonical_json(value: Any) -> bytes:
    """Reduce a JSON-primitive tree to a stable byte string.

    Sorted keys remove ordering sensitivity, compact separators remove
    whitespace variance, and ``allow_nan=False`` blocks the two values that
    would otherwise break hash stability. CPython's ``float.__repr__`` produces
    the shortest round-tripping representation and is platform-independent for
    IEEE-754 doubles, so float formatting agrees across machines.
    """
    _validate_json_value(value, "")
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def content_id_for(data: bytes) -> str:
    """The ``sha256:``-prefixed content id of raw bytes.

    Public, and named like its neighbours, because it stopped being an
    implementation detail two milestones ago. It was private through Milestone 3;
    Milestones 4 and 5 both reached past ``__all__`` for it
    (``from docdoc.kernel.identity import _sha256``) because a stage deriving its
    own artifact id needs exactly this and there was nothing else to call. A
    helper three layers import is not private, and leaving the underscore on it
    only meant the dependency was unreviewable.

    The derivation is unchanged, so every identity computed before this rename
    is byte-identical to one computed after it.
    """
    return ID_PREFIX + hashlib.sha256(data).hexdigest()


def blob_id_for(data: bytes) -> str:
    """Identity of a source file, derived from its bytes alone (FR-015)."""
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise IdentityError(
            f"blob identity requires bytes, got {type(data).__name__}",
            field="data",
        )
    return content_id_for(bytes(data))


def options_hash_for(options: Any) -> str:
    """Identity of a processing-options mapping (FR-018)."""
    return content_id_for(canonical_json(options))


def document_id_for(
    *,
    blob_id: str,
    parser_id: str,
    parser_version: str,
    options_hash: str,
) -> str:
    """Identity of one specific parse of one specific file (FR-016)."""
    for name, value in (
        ("blob_id", blob_id),
        ("parser_id", parser_id),
        ("parser_version", parser_version),
        ("options_hash", options_hash),
    ):
        if not isinstance(value, str) or not value:
            raise IdentityError(
                f"{name} must be a non-empty string",
                field=name,
                detail=f"got {value!r}",
            )
    payload = {
        "v": IDENTITY_SCHEMA_VERSION,
        "blob_id": blob_id,
        "parser_id": parser_id,
        "parser_version": parser_version,
        "options_hash": options_hash,
    }
    return content_id_for(canonical_json(payload))
