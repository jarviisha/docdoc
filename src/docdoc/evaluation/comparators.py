"""`comparators@1` — exact equality on the typed value, and the gate in front of it.

FR-023 fixes the MVP comparator set as exact equality on the typed value: decimals
compared as decimals, dates as dates, strings byte-for-byte. FR-024 forbids
normalizing, case-folding, trimming, rounding, or coercing across types to make a
value match.

**The type-identity gate is the requirement, not defensive coding.** In Python:

    True == 1              -> True
    Decimal(1) == True     -> True
    1 == 1.0               -> True

Without ``type(a) is type(b)`` a boolean label silently matches an integer
prediction and the report calls it correct -- which is precisely the cross-type
coercion FR-024 forbids, arriving through the back door. ``isinstance`` does not
help and is actively wrong here, because ``bool`` is a subclass of ``int``.

What the gate does **not** do is reject representational difference within a type.
``Decimal("1240.00") == Decimal("1240.0")`` is ``True`` and should be: trailing
zeros are representation, not value, and comparing *typed* values is what absorbs
that class of difference without anyone having to write a normalization rule.

Any future leniency -- case-folded strings, rounded decimals -- is a **new
comparator with a new version**, recorded in the report next to every metric it
affected. The registry below is keyed by ``FieldType`` so that adding one is a
data change and an identity move, never an ``if`` inside the scorer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docdoc.extraction.schema import FieldType

__all__ = ["COMPARATOR_VERSIONS", "EXACT", "comparator_version_for", "equal"]

#: The single comparator of the MVP. Named and versioned even though there is
#: only one, because FR-024 requires the version to travel with every metric it
#: decided -- and a set of one is where that discipline is cheapest to establish.
EXACT = "exact@1"

#: ``FieldType`` value -> comparator version. A string key rather than the enum
#: so the mapping survives serialization into ``options_hash`` unchanged.
COMPARATOR_VERSIONS: dict[str, str] = {
    "string": EXACT,
    "integer": EXACT,
    "number": EXACT,
    "boolean": EXACT,
    "date": EXACT,
    "datetime": EXACT,
    "decimal": EXACT,
}


def comparator_version_for(field_type: FieldType | str | None) -> str:
    """Which comparator decided a field, for the report to record."""
    if field_type is None:
        return EXACT
    return COMPARATOR_VERSIONS.get(str(field_type), EXACT)


def equal(expected: Any, predicted: Any) -> bool:
    """Exact equality on the typed value, with types gated first.

    ``type(...) is type(...)`` rather than ``isinstance``: ``bool`` subclasses
    ``int``, so an ``isinstance`` gate would let ``True`` match ``1`` and call it
    correct.
    """
    if type(expected) is not type(predicted):
        return False
    return bool(expected == predicted)
