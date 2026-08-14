"""Physical pages — the frame of reference for all geometry."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, field_validator

from docdoc.kernel.span import Span

__all__ = ["Page"]

_VALID_ROTATIONS = frozenset({0, 90, 180, 270})


class Page(BaseModel):
    """One page of a document.

    ``width`` and ``height`` are in the source's own units and are retained for
    provenance and for adapters converting native coordinates. The kernel never
    computes with them — geometry arrives already normalized (BB-3).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    index: int = Field(ge=0)
    span: Span
    width: float = Field(gt=0)
    height: float = Field(gt=0)
    rotation: int = 0

    @field_validator("rotation")
    @classmethod
    def _check_rotation(cls, value: int) -> int:
        if value not in _VALID_ROTATIONS:
            raise ValueError(f"rotation must be one of {sorted(_VALID_ROTATIONS)}, got {value}")
        return value

    @field_validator("span")
    @classmethod
    def _check_span(cls, value: Span) -> Span:
        return Span.create(value.start, value.end)
