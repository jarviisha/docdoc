"""Checking a model's answer against the schema it was asked for.

The provider constrains the response shape server-side, so this is the second of
two guarantees rather than the only one. It exists because "the provider promised"
and "the bytes that arrived" are different claims, and because a value still has
to *parse* to its declared type once the shape is right.

Nothing is repaired. A response that is not the requested shape, or that omits a
declared field, raises with the field path named (FR-007). Coercing it into place
would turn a model that misunderstood into a result that looks confident.

The plan called for compiling each schema to a ``pydantic`` model and caching it.
That turned out to be the more complicated of the two options: the type set is
small and closed (seven scalar types, three cardinalities), the values need
domain coercion anyway (``Decimal`` from a string, ``date`` from ISO-8601), and
``ValidationError`` paths would have to be translated back into field paths to
say anything useful. Walking the ``FieldSpec`` tree directly needs no compilation
step at all, which also removes the per-extraction recompile that the caching was
there to prevent.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from docdoc.extraction.errors import ExtractionError
from docdoc.extraction.schema import Cardinality, FieldSpec, FieldType, Schema
from docdoc.extraction.shape import CLAIMED_TEXT_KEY, CONFIDENCE_KEY, VALUE_KEY
from docdoc.extraction.value import ExtractedValue, ValueTree

__all__ = ["ConformanceReport", "conform"]


class ConformanceReport:
    """What conforming produced, plus what it discarded.

    ``discarded`` is the record FR-008 requires: an undeclared field never
    reaches the result, and "never reached the result" is not the same as
    "never happened".
    """

    __slots__ = ("discarded", "values")

    def __init__(self, *, values: ValueTree, discarded: tuple[str, ...]) -> None:
        self.values = values
        self.discarded = discarded


def conform(
    payload: Any,
    schema: Schema,
    *,
    document_id: str | None = None,
    adapter_id: str | None = None,
) -> ConformanceReport:
    """Turn a raw response into a value tree, or raise ``ExtractionError``."""
    discarded: list[str] = []
    context = _Context(
        schema_identity=schema.identity,
        document_id=document_id,
        adapter_id=adapter_id,
        discarded=discarded,
    )
    values = _object(payload, schema.fields, path="", context=context)
    return ConformanceReport(values=values, discarded=tuple(discarded))


class _Context:
    """Everything an error needs to name, carried once instead of per call.

    SC-012 requires each failure to name the document, the schema, and the
    responsible adapter; threading them as parameters would mean forgetting one
    somewhere.
    """

    __slots__ = ("adapter_id", "discarded", "document_id", "schema_identity")

    def __init__(
        self,
        *,
        schema_identity: str,
        document_id: str | None,
        adapter_id: str | None,
        discarded: list[str],
    ) -> None:
        self.schema_identity = schema_identity
        self.document_id = document_id
        self.adapter_id = adapter_id
        self.discarded = discarded

    def fail(self, message: str, *, reason: str, path: str) -> ExtractionError:
        return ExtractionError(
            message,
            reason=reason,
            document_id=self.document_id,
            schema_identity=self.schema_identity,
            adapter_id=self.adapter_id,
            field_path=path or None,
        )


def _object(
    payload: Any,
    fields: tuple[FieldSpec, ...],
    *,
    path: str,
    context: _Context,
) -> ValueTree:
    where = path or "<root>"
    if not isinstance(payload, dict):
        raise context.fail(
            f"expected an object at {where}, got {type(payload).__name__}",
            reason="shape",
            path=path,
        )

    declared = {field.name for field in fields}
    for key in payload:
        if key not in declared:
            context.discarded.append(f"{path}{key}" if path else key)

    tree: ValueTree = {}
    for field in fields:
        child_path = f"{path}{field.name}"
        if field.name not in payload:
            raise context.fail(
                f"response omits declared field {child_path!r}. Every declared field must be "
                "present; a field the document does not contain is reported as a null value, "
                "not as an absent key",
                reason="missing_field",
                path=child_path,
            )
        tree[field.name] = _field(payload[field.name], field, path=child_path, context=context)
    return tree


def _field(
    payload: Any,
    field: FieldSpec,
    *,
    path: str,
    context: _Context,
) -> ExtractedValue | ValueTree | tuple[ValueTree, ...]:
    if field.cardinality is Cardinality.SCALAR:
        return _scalar(payload, field, path=path, context=context)
    if field.cardinality is Cardinality.GROUP:
        return _object(payload, field.fields, path=f"{path}.", context=context)

    if not isinstance(payload, list):
        raise context.fail(
            f"expected a list at {path!r} for repeating group {field.name!r}, "
            f"got {type(payload).__name__}",
            reason="shape",
            path=path,
        )
    return tuple(
        _object(entry, field.fields, path=f"{path}[{index}].", context=context)
        for index, entry in enumerate(payload)
    )


def _scalar(
    payload: Any,
    field: FieldSpec,
    *,
    path: str,
    context: _Context,
) -> ExtractedValue:
    if not isinstance(payload, dict):
        raise context.fail(
            f"expected an object at {path!r} carrying {VALUE_KEY!r} and {CLAIMED_TEXT_KEY!r}, "
            f"got {type(payload).__name__}",
            reason="shape",
            path=path,
        )
    for key in (VALUE_KEY, CLAIMED_TEXT_KEY):
        if key not in payload:
            raise context.fail(
                f"response omits {key!r} at {path!r}",
                reason="shape",
                path=path,
            )

    raw_value = payload[VALUE_KEY]
    claimed = payload[CLAIMED_TEXT_KEY]
    if claimed is not None and not isinstance(claimed, str):
        raise context.fail(
            f"{CLAIMED_TEXT_KEY!r} at {path!r} must be a string or null, "
            f"got {type(claimed).__name__}",
            reason="shape",
            path=path,
        )

    confidence = payload.get(CONFIDENCE_KEY)
    if confidence is not None and not isinstance(confidence, (int, float)):
        raise context.fail(
            f"{CONFIDENCE_KEY!r} at {path!r} must be a number or null, "
            f"got {type(confidence).__name__}",
            reason="shape",
            path=path,
        )

    value = None if raw_value is None else _coerce(raw_value, field, path=path, context=context)

    return ExtractedValue(
        field_path=path,
        value=value,
        present=raw_value is not None,
        # Byte-faithful: no trimming, no case folding, no Unicode normalisation.
        # Milestone 4 cannot resolve text this layer has already altered (EXT-18).
        claimed_text=claimed,
        model_confidence=None if confidence is None else float(confidence),
    )


def _coerce(raw: Any, field: FieldSpec, *, path: str, context: _Context) -> Any:
    assert field.type is not None
    kind = field.type
    try:
        if kind is FieldType.STRING:
            if not isinstance(raw, str):
                raise TypeError(type(raw).__name__)
            return raw
        if kind is FieldType.BOOLEAN:
            if not isinstance(raw, bool):
                raise TypeError(type(raw).__name__)
            return raw
        if kind is FieldType.INTEGER:
            if isinstance(raw, bool) or not isinstance(raw, int):
                raise TypeError(type(raw).__name__)
            return raw
        if kind is FieldType.NUMBER:
            if isinstance(raw, bool) or not isinstance(raw, (int, float)):
                raise TypeError(type(raw).__name__)
            return float(raw)
        if kind is FieldType.DECIMAL:
            if not isinstance(raw, str):
                raise TypeError(type(raw).__name__)
            return Decimal(raw)
        if kind is FieldType.DATE:
            if not isinstance(raw, str):
                raise TypeError(type(raw).__name__)
            return date.fromisoformat(raw)
        if kind is FieldType.DATETIME:
            if not isinstance(raw, str):
                raise TypeError(type(raw).__name__)
            return datetime.fromisoformat(raw)
    except (TypeError, ValueError, InvalidOperation) as exc:
        raise context.fail(
            f"value at {path!r} does not parse as {kind}: {exc}",
            reason="type",
            path=path,
        ) from exc
    raise AssertionError(f"unhandled field type {kind}")  # pragma: no cover
