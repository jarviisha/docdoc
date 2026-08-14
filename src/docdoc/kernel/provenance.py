"""What produced a document, and what it was able to supply.

There is deliberately **no timestamp** here: the kernel cannot read the clock
(FR-020). Processing time is recorded by the pipeline layer, which may.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from docdoc.kernel.identity import canonical_json

__all__ = ["Capabilities", "IngestProvenance"]


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

    @field_validator("options")
    @classmethod
    def _check_options_are_encodable(cls, value: Mapping[str, Any]) -> Mapping[str, Any]:
        # Fail at construction rather than when the hash is next recomputed.
        canonical_json(dict(value))
        return dict(value)
