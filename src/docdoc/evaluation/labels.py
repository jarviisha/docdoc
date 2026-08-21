"""Labels — one stated truth for one field path.

A label says one of exactly two things about a field: it holds this value, or it
is correctly absent. A field path carrying no label is *unlabeled*, which is a
third state and not a synonym for either (EVA-8b).

Labels are data. They are authored in JSON, not constructed in Python, because a
golden set that requires programming to extend will not be extended (FR-022) --
the same reason Milestone 5 made validation rules schema data.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

from docdoc.kernel import BBox

__all__ = ["Expectation", "ExpectedLocation", "Label"]


class Expectation(StrEnum):
    """What a label asserts about its field."""

    VALUE = "value"
    ABSENT = "absent"


class ExpectedLocation(BaseModel):
    """Where a label says the value physically sits (EVA-7).

    A page, optionally narrowed by a box. **Never a text offset**, and the reason
    is not stylistic: offsets into ``Document.text`` move whenever a parser
    changes how it extracts text, so a label pinned to one would turn a parser
    upgrade into a mass mislocation that is not mislocation. The ink does not move
    when the parser changes; the offsets do (EVA-7a).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: 0-based, matching ``GroundingOutcome.pages``.
    page: int

    #: Optional. Where present, agreement additionally requires the recorded
    #: geometry to lie inside this box -- see :mod:`.location`.
    bbox: BBox | None = None


class Label(BaseModel):
    """One stated truth for one field path (EVA-8)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Milestone 3's field-path form, entry indices included:
    #: ``line_items[2].amount``. Deliberately the same form extraction,
    #: grounding, and validation already use, so a label addresses a field the
    #: way every other stage addresses it (FR-017).
    field_path: str

    expectation: Expectation

    #: Present if and only if ``expectation is VALUE``.
    value: Any = None

    #: Optional (FR-018). Where absent, only the grounding rate is measurable for
    #: this field and the report says which of the two it is reporting.
    location: ExpectedLocation | None = None

    #: Who stated it and when (FR-019). Neither can change a metric, so neither
    #: enters the dataset identity -- an identity that moved on a typo fix would
    #: break comparison for no reason (EVA-6).
    labeler: str | None = None
    labeled_at: datetime | None = None

    @model_validator(mode="after")
    def _value_matches_expectation(self) -> Label:
        if self.expectation is Expectation.VALUE and self.value is None:
            raise ValueError(
                f"label for {self.field_path!r} expects a value but states none; "
                "use expectation='absent' to assert the field is correctly absent"
            )
        if self.expectation is Expectation.ABSENT and self.value is not None:
            raise ValueError(
                f"label for {self.field_path!r} asserts absence but carries the "
                f"value {self.value!r}"
            )
        return self
