"""What a parser can supply, and how a caller asks for it.

This is the vocabulary that replaces provider names. Application code says "I
need geometry for a PDF"; it never says "use Azure" (Principle IV, FR-015).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from docdoc.kernel import Capabilities

__all__ = ["CapabilityRequest", "ParserCapabilities"]

#: The four capability names, in the order they are reported in errors.
CAPABILITY_NAMES = ("text", "geometry", "tables", "handwriting")


class ParserCapabilities(BaseModel):
    """What a parser declares it can supply, and for which media types."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: bool = True
    geometry: bool = False
    tables: bool = False
    handwriting: bool = False
    media_types: frozenset[str] = Field(default_factory=frozenset)
    #: Drives the offline-first default priority order (R11). Not a capability
    #: a caller requests -- a property of the parser a deployment ranks by.
    requires_network: bool = False

    def to_kernel(self) -> Capabilities:
        """The kernel's view, which is the four booleans and nothing else."""
        return Capabilities(
            text=self.text,
            geometry=self.geometry,
            tables=self.tables,
            handwriting=self.handwriting,
        )

    def satisfies(self, request: CapabilityRequest) -> bool:
        """Whether this parser can serve the request.

        Declaring *more* than was asked for is fine; declaring less is not.
        """
        if request.media_type not in self.media_types:
            return False
        return all(getattr(self, name) or not getattr(request, name) for name in CAPABILITY_NAMES)

    def missing_for(self, request: CapabilityRequest) -> tuple[str, ...]:
        """The requested capabilities this parser does not declare.

        Used to explain a selection failure in terms of capabilities rather than
        provider names (FR-017).
        """
        return tuple(
            name for name in CAPABILITY_NAMES if getattr(request, name) and not getattr(self, name)
        )


class CapabilityRequest(BaseModel):
    """What a caller needs. The only supported way to choose a parser."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    media_type: str = Field(min_length=1)
    text: bool = True
    geometry: bool = False
    tables: bool = False
    handwriting: bool = False

    def required_names(self) -> tuple[str, ...]:
        return tuple(name for name in CAPABILITY_NAMES if getattr(self, name))
