"""T020 — the walk that turns a schema and a tree into a list of obligations.

Everything downstream counts what this produces, so two things are asserted:
completeness (one slot per declared field per place) and stability (the same
inputs always yield the same order).
"""

from __future__ import annotations

import pytest

from docdoc.validation.enumerate import walk
from docdoc.validation.errors import ValidationError
from tests.fixtures.validation import artifacts


@pytest.fixture(scope="module")
def pair():
    return artifacts.build()


def test_every_declared_field_appears_once_per_place(pair) -> None:
    index = walk(pair.schema, pair.extraction.values)
    paths = [slot.path for slot in index.slots]
    assert paths == [
        "number",
        "currency",
        "issue_date",
        "due_date",
        "total",
        "notes",
        "supplier",
        "supplier.name",
        "supplier.tax_id",
        "line_items",
        "line_items[0].description",
        "line_items[0].quantity",
        "line_items[0].unit_price",
        "line_items[0].amount",
        "line_items[1].description",
        "line_items[1].quantity",
        "line_items[1].unit_price",
        "line_items[1].amount",
    ]


def test_the_order_is_declaration_order_depth_first(pair) -> None:
    """A schema author reading their own file can predict the findings list."""
    index = walk(pair.schema, pair.extraction.values)
    assert index.order["number"] < index.order["supplier.name"]
    assert index.order["supplier"] < index.order["supplier.name"]
    assert index.order["line_items"] < index.order["line_items[0].amount"]
    assert index.order["line_items[0].amount"] < index.order["line_items[1].description"]


def test_repeating_entries_are_counted(pair) -> None:
    index = walk(pair.schema, pair.extraction.values)
    assert index.entry_counts == {"line_items": 2}


def test_walking_twice_produces_the_same_order(pair) -> None:
    first = walk(pair.schema, pair.extraction.values)
    second = walk(pair.schema, pair.extraction.values)
    assert [slot.path for slot in first.slots] == [slot.path for slot in second.slots]


def test_a_group_whose_every_field_is_absent_is_absent(pair) -> None:
    stripped = dict(pair.extraction.values)
    supplier = dict(stripped["supplier"])
    supplier["name"] = supplier["name"].model_copy(update={"present": False, "value": None})
    supplier["tax_id"] = supplier["tax_id"].model_copy(update={"present": False, "value": None})
    stripped["supplier"] = supplier
    index = walk(pair.schema, stripped)
    assert next(slot for slot in index.slots if slot.path == "supplier").group_absent


def test_a_group_with_one_present_field_is_present(pair) -> None:
    index = walk(pair.schema, pair.extraction.values)
    assert not next(slot for slot in index.slots if slot.path == "supplier").group_absent


def test_zero_entries_is_a_legal_repeating_group(pair) -> None:
    empty = dict(pair.extraction.values)
    empty["line_items"] = ()
    index = walk(pair.schema, empty)
    assert index.entry_counts == {"line_items": 0}
    assert not any(slot.path.startswith("line_items[") for slot in index.slots)


class TestShapeRefusals:
    """FR-018 — a tree that does not fit its schema is a refusal, not a finding."""

    def test_a_missing_declared_field_is_refused(self, pair) -> None:
        broken = {key: value for key, value in pair.extraction.values.items() if key != "total"}
        with pytest.raises(ValidationError, match="total"):
            walk(pair.schema, broken)

    def test_a_scalar_where_a_group_was_declared_is_refused(self, pair) -> None:
        broken = dict(pair.extraction.values)
        broken["supplier"] = broken["number"]
        with pytest.raises(ValidationError, match="supplier"):
            walk(pair.schema, broken)

    def test_a_group_where_a_repeating_group_was_declared_is_refused(self, pair) -> None:
        broken = dict(pair.extraction.values)
        broken["line_items"] = {"description": broken["number"]}
        with pytest.raises(ValidationError, match="repeating group"):
            walk(pair.schema, broken)

    def test_a_group_where_a_scalar_was_declared_is_refused(self, pair) -> None:
        """T112, FR-018 — the third direction, which no test tried.

        Disabling this branch of `_field` survived a mutation run: the other two
        refusals cover a scalar where a *group* belongs and a group where a
        *repeating group* belongs, so a dict arriving under a scalar field was
        never attempted. Without the check the walk reaches `node.present` on a
        plain dict and dies with an `AttributeError` — an untyped failure, several
        frames from the artifacts that caused it.
        """
        broken = dict(pair.extraction.values)
        broken["number"] = {"nested": broken["number"]}
        with pytest.raises(ValidationError, match="number"):
            walk(pair.schema, broken)

    def test_a_repeating_group_where_a_scalar_was_declared_is_refused(self, pair) -> None:
        """The same direction with a tuple, which is how a repeating group arrives."""
        broken = dict(pair.extraction.values)
        broken["total"] = (dict(pair.extraction.values["supplier"]),)
        with pytest.raises(ValidationError, match="total"):
            walk(pair.schema, broken)

    def test_the_refusal_names_the_field(self, pair) -> None:
        broken = {key: value for key, value in pair.extraction.values.items() if key != "notes"}
        with pytest.raises(ValidationError) as caught:
            walk(pair.schema, broken)
        assert caught.value.field_path == "notes"


def test_a_zero_field_schema_enumerates_nothing() -> None:
    from docdoc.extraction.schema import Schema

    index = walk(Schema(name="empty", version=1), {})
    assert index.slots == ()
    assert index.order == {}
