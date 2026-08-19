"""How much a finding is allowed to mean.

Its own module because both ``options`` and ``result`` need it and neither may
import the other: the options are part of what a result records, so the
dependency runs one way.
"""

from __future__ import annotations

from enum import StrEnum

__all__ = ["Severity"]


class Severity(StrEnum):
    """Only ``ERROR`` moves the verdict.

    ``WARNING`` and ``INFO`` are recorded, counted, and deliberately powerless. A
    stage that let a warning quietly fail a document would be making a routing
    decision, and routing is policy built on this verdict rather than part of
    producing it (FR-046).
    """

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"
