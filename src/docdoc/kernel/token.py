"""The smallest addressable unit of a document.

A ``Token`` carries **no text of its own**. Its text is
``document.text[token.span.start:token.span.end]``.

Storing the text on the token would create a second copy of the entire document
and, worse, a duplicate-state invariant (``token.text == text[span]``) that
could drift. Deriving it removes that failure mode outright and cuts memory
materially on large scans (research.md, "Deviations from FIRST_DOC.md").
"""

from __future__ import annotations

from typing import NamedTuple

from docdoc.kernel.errors import GeometryError
from docdoc.kernel.geometry import Geometry
from docdoc.kernel.span import Span

__all__ = ["Token"]


class Token(NamedTuple):
    """A span of text with optional geometry and source-reported confidence.

    ``geometry`` is ``None`` exactly when the producing parser lacks the geometry
    capability. Partial geometry within one document is rejected at construction
    (DOC-8), so this field is uniform across a document's tokens.

    ``source_confidence`` is stored verbatim and never interpreted here — the
    same treatment ADR-0004 gives model-reported confidence.
    """

    span: Span
    geometry: Geometry | None = None
    source_confidence: float | None = None

    @classmethod
    def create(
        cls,
        span: Span,
        geometry: Geometry | None = None,
        source_confidence: float | None = None,
    ) -> Token:
        """Construct a token, enforcing TK-2."""
        if source_confidence is not None:
            if not isinstance(source_confidence, (int, float)) or isinstance(
                source_confidence, bool
            ):
                raise GeometryError(
                    f"source_confidence must be a number, got {type(source_confidence).__name__}"
                )
            if not 0.0 <= source_confidence <= 1.0:
                raise GeometryError(
                    f"source_confidence must be within 0.0..1.0, got {source_confidence}"
                )
        return cls(span, geometry, source_confidence)
