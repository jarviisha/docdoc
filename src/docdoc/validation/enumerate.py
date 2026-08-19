"""Turning a schema and a value tree into an explicit list of obligations.

Everything downstream counts what this module produces, so two properties matter
more than its brevity:

* **Order is fixed.** Declaration order, depth first, entering repeating groups
  in entry order. A findings list a schema author can predict while reading their
  own file, and one that cannot vary with dict iteration order (FR-043).
* **The walk refuses a tree that does not fit.** A node the schema declares and
  the tree lacks, or a group answered as a value, raises rather than being
  skipped. The traversal has to happen anyway, so the check costs an ``if``, and
  the alternative is a confident verdict over two artifacts that do not belong
  together (FR-018).

**An obligation exists only where it applies.** A constraint constrains a value;
where the model reported no value there is nothing to constrain, so no constraint
check is declared. That is why an optional field left absent produces no
`not_evaluated` entry -- if it did, `incomplete` would stop meaning "an obligation
went unchecked" and start meaning "some optional field was missing", which is
true of nearly every real document. Absence is the requiredness check's subject,
and it is the one that reports it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from docdoc.extraction.schema import Cardinality, FieldSpec, Schema
from docdoc.extraction.value import ExtractedValue
from docdoc.validation.errors import ValidationError

__all__ = ["Slot", "ValueIndex", "walk"]


@dataclass(frozen=True, slots=True)
class Slot:
    """One field at one place in the result.

    ``path`` carries entry indices (``line_items[2].amount``) in the same form
    Milestone 4 uses, so a finding, a grounding outcome, and a check all address
    a value the same way.
    """

    path: str
    field: FieldSpec

    #: The extracted value for a scalar; ``None`` for a grouping field.
    value: ExtractedValue | None = None

    #: Entry count for a repeating group; ``None`` for anything else.
    entries: int | None = None

    #: For a grouping field: every scalar inside it was reported absent.
    group_absent: bool = False

    @property
    def present(self) -> bool:
        return self.value is not None and self.value.present


@dataclass(frozen=True, slots=True)
class ValueIndex:
    """The walk's output: ordered slots, plus the lookups rules need."""

    slots: tuple[Slot, ...]

    #: Every scalar, by indexed path.
    values: dict[str, ExtractedValue]

    #: Entry count per repeating-group path.
    entry_counts: dict[str, int]

    #: Position of each path in the walk, which is the total order findings use.
    order: dict[str, int]


def walk(schema: Schema, values: dict[str, Any]) -> ValueIndex:
    """Enumerate every field the schema declares, in declaration order.

    Raises:
        ValidationError: the value tree does not fit the schema (FR-018).
    """
    slots: list[Slot] = []
    _fields(schema.fields, values, prefix="", slots=slots)
    indexed = {slot.path: slot.value for slot in slots if slot.value is not None}
    counts = {slot.path: slot.entries for slot in slots if slot.entries is not None}
    order = {slot.path: position for position, slot in enumerate(slots)}
    return ValueIndex(tuple(slots), indexed, counts, order)


def _fields(
    fields: tuple[FieldSpec, ...],
    node: Any,
    *,
    prefix: str,
    slots: list[Slot],
) -> bool:
    """Walk one object level. Returns whether anything inside it was present."""
    if not isinstance(node, dict):
        raise ValidationError(
            f"the extraction result holds {type(node).__name__} where the schema declares "
            f"an object at {prefix or '<root>'!r}. These two artifacts do not fit together",
            field_path=prefix or None,
        )
    any_present = False
    for field in fields:
        path = f"{prefix}{field.name}"
        if field.name not in node:
            raise ValidationError(
                f"the extraction result has no {path!r}, which the schema declares. "
                "Milestone 3 emits every declared field, so a missing one means the "
                "result was produced under a different schema",
                field_path=path,
            )
        any_present |= _field(field, node[field.name], path=path, slots=slots)
    return any_present


def _field(field: FieldSpec, node: Any, *, path: str, slots: list[Slot]) -> bool:
    if field.cardinality is Cardinality.SCALAR:
        if not isinstance(node, ExtractedValue):
            raise ValidationError(
                f"the extraction result holds {type(node).__name__} where the schema "
                f"declares a scalar at {path!r}",
                field_path=path,
            )
        slots.append(Slot(path=path, field=field, value=node))
        return node.present

    if field.cardinality is Cardinality.GROUP:
        # The group's own slot is appended first, then patched with what the
        # walk of its children discovered. Appending after would put the group
        # behind its own fields in the order, and a finding about a group should
        # read before the findings about what is inside it.
        position = len(slots)
        slots.append(Slot(path=path, field=field))
        present = _fields(field.fields, node, prefix=f"{path}.", slots=slots)
        slots[position] = Slot(path=path, field=field, group_absent=not present)
        return present

    if not isinstance(node, tuple):
        raise ValidationError(
            f"the extraction result holds {type(node).__name__} where the schema declares "
            f"a repeating group at {path!r}",
            field_path=path,
        )
    position = len(slots)
    slots.append(Slot(path=path, field=field, entries=len(node)))
    present = False
    for index, entry in enumerate(node):
        present |= _fields(field.fields, entry, prefix=f"{path}[{index}].", slots=slots)
    slots[position] = Slot(path=path, field=field, entries=len(node), group_absent=not present)
    return present
