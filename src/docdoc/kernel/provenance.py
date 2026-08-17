"""What produced a document, and what it was able to supply.

There is deliberately **no timestamp** here: the kernel cannot read the clock
(FR-020). Processing time is recorded by the pipeline layer, which may.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from docdoc.kernel.identity import canonical_json

__all__ = ["Capabilities", "IngestProvenance", "PageTextVerdict", "TextLayerRecord"]


class Capabilities(BaseModel):
    """What the producing parser was able to supply.

    This drives :class:`~docdoc.kernel.errors.CapabilityError`: requesting
    geometry from a document whose parser had none raises, rather than returning
    an empty result a caller would misread as "nothing there" (FR-022).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: bool
    geometry: bool
    tables: bool
    handwriting: bool


class PageTextVerdict(NamedTuple):
    """What the text-layer rule found on one page.

    A ``NamedTuple`` because there is one per page and documents run to a
    thousand pages -- the same reason :class:`~docdoc.kernel.token.Token` is one.
    """

    page_index: int
    char_count: int
    text_bearing: bool


class TextLayerRecord(BaseModel):
    """The text-layer verdict, its evidence, and the rule that produced it.

    Lives in the kernel because Principle I puts ingestion provenance *inside*
    the document rather than beside it: a side-car record would be separable
    from the document it explains, which is how provenance gets lost. It is pure
    data -- the ingest layer computes it, the kernel only carries it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Identifies the rule *and its thresholds*, so a later retune is detectable
    #: in results produced before it (FR-010).
    rule_id: str = Field(min_length=1)
    min_chars_per_page: int = Field(ge=0)
    min_text_bearing_fraction: float = Field(ge=0.0, le=1.0)
    #: One entry per page -- empty only when the rule could not run.
    pages: tuple[PageTextVerdict, ...] = ()
    #: The document-level verdict that decided routing.
    text_layer_usable: bool
    overridden: bool = False
    #: What the rule said, when a caller overrode it. ``None`` when the rule
    #: never ran.
    overridden_verdict: bool | None = None
    #: Why the rule was skipped, when it was -- e.g. ``"reader_unavailable"``.
    rule_not_run: str | None = None

    @field_validator("pages")
    @classmethod
    def _check_pages_are_ordered(
        cls, value: tuple[PageTextVerdict, ...]
    ) -> tuple[PageTextVerdict, ...]:
        # A verdict list that skips or repeats a page would make "one entry per
        # page" (ING-11) unverifiable by the caller.
        expected = tuple(range(len(value)))
        actual = tuple(verdict.page_index for verdict in value)
        if actual != expected:
            raise ValueError(f"page verdicts must be one per page in ascending order, got {actual}")
        return value


class IngestProvenance(BaseModel):
    """The record of how a document came to exist."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parser_id: str = Field(min_length=1)
    parser_version: str = Field(min_length=1)
    options: Mapping[str, Any] = Field(default_factory=dict)
    options_hash: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    capabilities: Capabilities
    #: Whether a native text layer was used rather than recognition (Principle V).
    text_layer_used: bool
    #: The full verdict behind ``text_layer_used``, including per-page evidence.
    #: Optional so every document Milestone 1 can construct stays valid; the
    #: ingest layer always sets it (ING-19).
    text_layer: TextLayerRecord | None = None
    #: The reading order the producing parser declared, e.g.
    #: ``"pymupdf-stream@1"``. Two parsers may legitimately order the same file
    #: differently; this says which ordering produced this text (FR-036).
    reading_order: str | None = None

    @model_validator(mode="after")
    def _check_verdict_agrees_with_summary(self) -> IngestProvenance:
        # ING-18: the summary bool and the detailed record must not disagree.
        # An override is exempt -- there the record deliberately holds the
        # verdict that was *not* acted on.
        record = self.text_layer
        if record is None or record.overridden or record.rule_not_run is not None:
            return self
        if record.text_layer_usable != self.text_layer_used:
            raise ValueError(
                "text_layer_used contradicts text_layer.text_layer_usable "
                f"({self.text_layer_used} vs {record.text_layer_usable}); an unforced "
                "route must follow its own verdict"
            )
        return self

    @field_validator("options")
    @classmethod
    def _check_options_are_encodable(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        # Fail at construction rather than when the hash is next recomputed.
        canonical_json(dict(value))
        return dict(value)
