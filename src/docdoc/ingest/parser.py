"""The contract every parser satisfies.

A parser is anything that turns a source file into a Document. The native PDF
reader and a cloud document-intelligence service are two instances of the same
protocol, which is what lets selection be capability-based (Principle IV) and
what keeps a third-party parser a first-class citizen rather than a special case.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Mapping

    from docdoc.ingest.capabilities import ParserCapabilities
    from docdoc.ingest.options import TransportSettings
    from docdoc.ingest.source import SourceFile
    from docdoc.kernel import Document, TextLayerRecord

__all__ = ["Parser"]


@runtime_checkable
class Parser(Protocol):
    """Produces exactly one Document, or raises. Never a partial one (ING-7)."""

    # Declared read-only, because they are: an adapter fixes them at class
    # definition and document identity depends on two of them. A plain class
    # attribute satisfies a read-only property, so implementations stay simple.

    @property
    def id(self) -> str:
        """Stable and provider-neutral: ``"pdf-text"``, ``"azure-di"``."""
        ...

    @property
    def version(self) -> str:
        """The adapter's own version *plus* the underlying library or service
        API version -- ``"1.0.0+pymupdf-1.28.2"``. A library upgrade that changes
        extraction output changes document identity, which is the point of a
        content-addressed chain (ADR-0003, FR-020)."""
        ...

    @property
    def capabilities(self) -> ParserCapabilities:
        """What this parser can supply, and for which media types."""
        ...

    @property
    def reading_order(self) -> str:
        """The ordering this parser emits tokens in, e.g.
        ``"pymupdf-stream@1"``. Recorded in provenance so the ordering behind
        any result is knowable after the fact (FR-036)."""
        ...

    def parse(
        self,
        source: SourceFile,
        options: Mapping[str, Any],
        transport: TransportSettings,
        text_layer: TextLayerRecord | None = None,
    ) -> Document:
        """Turn the source into a Document.

        Implementations that run offline ignore ``transport`` entirely.

        ``text_layer`` is the routing verdict, which only the caller of the
        ingest layer knows -- a parser cannot compute why it was chosen. It is
        passed in rather than attached afterwards because ``Document`` is
        immutable and provenance is part of it: there is no later moment at
        which it could be added without rebuilding the document.
        """
        ...
