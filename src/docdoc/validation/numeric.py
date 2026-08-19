"""All arithmetic in one place, in ``Decimal``, and honest about what it inherits.

FR-022 forbids this stage from evaluating a comparison in binary floating point.
It cannot undo one that already happened: Milestone 3's ``conform`` parses a
``decimal`` field to ``Decimal`` and an ``integer`` to ``int``, but a ``number``
field to a Python ``float``, so a value declared as ``number`` arrives with its
precision already spent.

What this module does about that:

* A ``float`` enters through ``Decimal(str(value))``, **never** ``Decimal(value)``.
  Measured: ``Decimal(str(1240.10))`` is ``1240.10`` while ``Decimal(1240.10)`` is
  ``1240.09999999999990905052982270717620849609375``. ``repr`` of a float is the
  shortest string that round-trips, so this is stable on every platform, which is
  what FR-051 needs.
* The documentation says plainly that ``number`` is lossy **by declaration** and
  that ``decimal`` is the type for money. A guarantee the type system contradicts
  would be worse than the honest sentence.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

__all__ = ["as_decimal", "render", "within_tolerance"]


def as_decimal(value: Any) -> Decimal | None:
    """A declared numeric value as an exact ``Decimal``, or ``None`` if it is not one.

    ``None`` rather than a raise: an operand of the wrong type is a check that
    *could not be evaluated*, which the caller reports with a reason code, not an
    exception. ``bool`` is excluded deliberately -- it is an ``int`` subclass in
    Python, and letting ``True`` sum as ``1`` would make a boolean field silently
    participate in arithmetic.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, float):
        return Decimal(str(value))
    return None


def within_tolerance(left: Decimal, right: Decimal, tolerance: Decimal) -> bool:
    """``|left - right| <= tolerance``, exactly.

    A zero tolerance is exact equality, and exact equality on ``Decimal`` already
    ignores scale: ``1240.0`` equals ``1240.00``. Nothing here rounds first,
    because rounding to compare is how a cent goes missing and the verdict says
    it did not.
    """
    return abs(left - right) <= tolerance


def render(value: Any) -> str:
    """A value as the canonical text a finding carries.

    Two runs over the same input must produce the same ``expected`` and
    ``actual`` strings, so this never uses ``repr`` of a float and never depends
    on a locale. Trailing zeros are preserved for a ``Decimal``: an amount
    written ``1240.00`` in the document should read that way in the finding
    about it.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if value is None:
        return "absent"
    return str(value)
