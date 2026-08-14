"""Tabular structure, retained when the producing parser supplies it.

Tables are optional. Their absence is a normal condition rather than an error
(TB-4) — many documents have none, and many parsers cannot detect them.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from docdoc.kernel.geometry import Geometry
from docdoc.kernel.span import Span

__all__ = ["Table", "TableCell"]


class TableCell(BaseModel):
    """One cell, traceable to the text range it covers."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    span: Span
    row: int = Field(ge=0)
    column: int = Field(ge=0)
    row_span: int = Field(default=1, ge=1)
    column_span: int = Field(default=1, ge=1)
    geometry: Geometry | None = None


class Table(BaseModel):
    """A grid of cells on a single page."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    span: Span
    page_index: int = Field(ge=0)
    n_rows: int = Field(ge=0)
    n_columns: int = Field(ge=0)
    cells: tuple[TableCell, ...] = ()
    geometry: Geometry | None = None

    @model_validator(mode="after")
    def _check_grid(self) -> Table:
        occupied: set[tuple[int, int]] = set()
        for cell in self.cells:
            if cell.row >= self.n_rows or cell.column >= self.n_columns:
                raise ValueError(
                    f"cell at ({cell.row}, {cell.column}) falls outside a "
                    f"{self.n_rows}x{self.n_columns} grid"
                )
            for r in range(cell.row, min(cell.row + cell.row_span, self.n_rows)):
                for c in range(cell.column, min(cell.column + cell.column_span, self.n_columns)):
                    if (r, c) in occupied:
                        raise ValueError(f"two cells occupy grid position ({r}, {c})")
                    occupied.add((r, c))
        return self
