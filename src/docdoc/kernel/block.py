"""Structural regions of a document.

Unlike tokens, **blocks may overlap** — a table block legitimately contains
paragraph blocks. They are therefore stored flat and are not part of the
:class:`~docdoc.kernel.span_index.SpanIndex`, whose binary search depends on
non-overlap.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from docdoc.kernel.geometry import Geometry
from docdoc.kernel.span import Span

__all__ = ["Block", "BlockKind"]


class BlockKind(StrEnum):
    PARAGRAPH = "paragraph"
    HEADING = "heading"
    LIST_ITEM = "list_item"
    TABLE = "table"
    FIGURE = "figure"
    HEADER = "header"
    FOOTER = "footer"
    CAPTION = "caption"
    OTHER = "other"


class Block(BaseModel):
    """A structural region such as a paragraph or heading."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    span: Span
    kind: BlockKind
    page_index: int = Field(ge=0)
    geometry: Geometry | None = None
