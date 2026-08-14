"""Normalized page geometry.

Coordinates are normalized to ``0.0..1.0`` with a **top-left origin**, so that
``y`` increases downward. Provider-native coordinate systems are converted in
the adapter; absolute units never reach the kernel (FR-005, BB-3).
"""

from __future__ import annotations

import math
from typing import NamedTuple

from docdoc.kernel.errors import GeometryError

__all__ = ["BBox", "Geometry"]


class BBox(NamedTuple):
    """A rectangle on a page, in normalized coordinates.

    Zero-area boxes are valid (BB-4): a zero-width token is a real thing that
    parsers emit, and rejecting it would force adapters to invent a width.
    """

    x0: float
    y0: float
    x1: float
    y1: float

    @classmethod
    def create(cls, x0: float, y0: float, x1: float, y1: float) -> BBox:
        """Construct a box, enforcing BB-1 and BB-2."""
        coords = (("x0", x0), ("y0", y0), ("x1", x1), ("y1", y1))
        for name, value in coords:
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                raise GeometryError(
                    f"bbox {name} must be a number, got {type(value).__name__}",
                    bbox=(x0, y0, x1, y1),
                )
            if not math.isfinite(value):
                raise GeometryError(
                    f"bbox {name} must be finite, got {value}", bbox=(x0, y0, x1, y1)
                )
            if not 0.0 <= value <= 1.0:
                raise GeometryError(
                    f"bbox {name} must be normalized to 0.0..1.0, got {value}",
                    bbox=(x0, y0, x1, y1),
                )
        if x1 < x0:
            raise GeometryError(f"bbox x1 precedes x0: {x0} > {x1}", bbox=(x0, y0, x1, y1))
        if y1 < y0:
            raise GeometryError(f"bbox y1 precedes y0: {y0} > {y1}", bbox=(x0, y0, x1, y1))
        return cls(x0, y0, x1, y1)

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    @property
    def area(self) -> float:
        return self.width * self.height

    def union(self, other: BBox) -> BBox:
        """The smallest box covering both."""
        return BBox(
            min(self.x0, other.x0),
            min(self.y0, other.y0),
            max(self.x1, other.x1),
            max(self.y1, other.y1),
        )

    def intersects(self, other: BBox) -> bool:
        """Whether the two boxes overlap. Touching edges do not count."""
        return (
            self.x0 < other.x1 and self.x1 > other.x0 and self.y0 < other.y1 and self.y1 > other.y0
        )


class Geometry(NamedTuple):
    """A box anchored to a specific page.

    A :class:`BBox` alone is never meaningful — the same coordinates describe
    different physical locations on different pages.
    """

    page_index: int
    bbox: BBox

    @classmethod
    def create(cls, page_index: int, bbox: BBox) -> Geometry:
        """Construct page-anchored geometry, enforcing GE-1."""
        if not isinstance(page_index, int) or isinstance(page_index, bool):
            raise GeometryError(
                f"page_index must be an int, got {type(page_index).__name__}",
                bbox=bbox,
            )
        if page_index < 0:
            raise GeometryError(
                f"page_index must be non-negative, got {page_index}",
                bbox=bbox,
                page_index=page_index,
            )
        return cls(page_index, bbox)
