"""What one extracted field is, and what it deliberately is not.

ADR-0004 keeps two trust levels apart by giving them separate fields rather than
one blended number. The grounding fields are present here and **unresolved** --
this milestone computes none of them, not even the exact tier the kernel's
existing search could satisfy cheaply.

That abstention is structural, not a matter of effort. ADR-0003 makes grounding
its own stage with its own artifact and its own ``grounding_version``; resolving
it here would collapse two stages and fold a grounding input into the extract
artifact's identity. Even the exact tier needs the tie-break rule ADR-0005
specifies for a claimed text that appears more than once, and inventing a
temporary one would change results under an unchanged ``grounding_version``.

The consequence to accept: when this milestone ships, every extracted value is
ungrounded, so the product's central claim is not demonstrable end to end until
Milestone 4.
"""

from __future__ import annotations

from typing import Any, TypeAlias

from pydantic import BaseModel, ConfigDict

__all__ = ["ExtractedValue", "ValueTree"]


class ExtractedValue(BaseModel):
    """One field's outcome."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    field_path: str

    #: The typed value, or ``None`` for an explicit absence.
    value: Any = None

    #: Whether the model reported a value at all. This is what distinguishes an
    #: absence from a returned-empty value: ``present=False`` means the document
    #: does not contain the field, ``present=True`` with ``value=""`` means it
    #: contains an empty one (EXT-16).
    present: bool = False

    #: The text the model claims it read the value from, byte-for-byte as it was
    #: returned. Milestone 4 resolves *this* to spans and geometry, so text this
    #: layer altered could not be located (EXT-18, FR-003).
    claimed_text: str | None = None

    #: **Untrusted.** The model's own self-report, stored verbatim and passed
    #: through. It routes nothing, gates nothing, and is not a quality metric
    #: (ADR-0004, FR-031). It is recorded so a later calibrator can be fitted
    #: against it.
    model_confidence: float | None = None

    #: Trusted, deterministic, and **unresolved at this milestone** (EXT-24).
    grounding: str | None = None
    grounding_score: float | None = None

    #: Reserved by ADR-0004. Always ``None`` in the MVP; a blended score may only
    #: ever be produced by a versioned calibrator writing here, never by
    #: overwriting one of the inputs above.
    calibrated_confidence: float | None = None
    calibrator_version: str | None = None

    @property
    def grounded(self) -> bool:
        """Always ``False`` here. Kept so the question has one answer, not two.

        A caller that asks this before Milestone 4 gets a truthful "no" rather
        than an ``AttributeError``, and a caller that asks after gets the real
        answer without changing the call site.
        """
        return self.grounding is not None and self.grounding != "ungrounded"


#: A field name maps to a value, a nested group, or a tuple of repeating-group
#: entries. Recursive by declaration; bounded to one level of *repetition* by
#: EXT-3, which is checked when the schema is constructed rather than here.
ValueTree: TypeAlias = "dict[str, ExtractedValue | ValueTree | tuple[ValueTree, ...]]"
