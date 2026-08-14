"""Kernel error hierarchy.

Every error carries structured attributes, not just a formatted message, so
higher layers can translate them to API responses without parsing strings
(FR-023). ``CapabilityError`` in particular is the mechanism enforcing the
constitution's no-silent-fallback rule: an unavailable capability raises rather
than returning an empty stand-in.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from docdoc.kernel.geometry import BBox
    from docdoc.kernel.span import Span

__all__ = [
    "CapabilityError",
    "DocdocError",
    "DocumentInvariantError",
    "GeometryError",
    "IdentityError",
    "KernelError",
    "MergeError",
    "SpanError",
]


class DocdocError(Exception):
    """Root of every error docdoc raises.

    Consumers can catch this to handle anything originating in docdoc, without
    catching unrelated exceptions.
    """


class KernelError(DocdocError):
    """Root of every error raised by the kernel layer."""


class SpanError(KernelError):
    """A text range is malformed or does not fit the text it is used against."""

    def __init__(
        self,
        message: str,
        *,
        span: Span | tuple[int, int] | None = None,
        text_length: int | None = None,
    ) -> None:
        super().__init__(message)
        self.span = span
        self.text_length = text_length


class GeometryError(KernelError):
    """A bounding box or page anchor is outside the normalized coordinate model."""

    def __init__(
        self,
        message: str,
        *,
        bbox: BBox | tuple[float, float, float, float] | None = None,
        page_index: int | None = None,
    ) -> None:
        super().__init__(message)
        self.bbox = bbox
        self.page_index = page_index


class DocumentInvariantError(KernelError):
    """A document violates one of the DOC-1..DOC-8 construction invariants.

    ``rule`` is the stable identifier from data-model.md, so callers and tests
    can assert on the specific invariant rather than on message text.
    """

    def __init__(self, message: str, *, rule: str, detail: str = "") -> None:
        super().__init__(message)
        self.rule = rule
        self.detail = detail


class MergeError(KernelError):
    """Parts cannot be reassembled into a single document."""

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        part_ids: tuple[str, ...] = (),
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.part_ids = part_ids


class CapabilityError(KernelError):
    """A requested capability was not supplied by whatever produced the document.

    Raised instead of returning an empty or default result, so a caller can
    never mistake "unavailable" for "absent" (FR-022).
    """

    def __init__(
        self,
        message: str,
        *,
        capability: str,
        available: bool,
        parser_id: str,
    ) -> None:
        super().__init__(message)
        self.capability = capability
        self.available = available
        self.parser_id = parser_id


class IdentityError(KernelError):
    """A value cannot be reduced to a stable content-addressed identity."""

    def __init__(self, message: str, *, field: str, detail: str = "") -> None:
        super().__init__(message)
        self.field = field
        self.detail = detail
