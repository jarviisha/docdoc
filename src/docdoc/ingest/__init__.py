"""docdoc ingest — turning a source file into the canonical Document IR.

This module is the public contract (see
``specs/002-ingest-parser-layer/contracts/ingest-api.md``). Anything not exported
here is private and may change without notice.

The layer takes bytes and returns a :class:`docdoc.kernel.Document`. It never
persists, never caches, never extracts, and never grounds. Provider SDKs live in
``docdoc.ingest.parsers`` and nowhere else, which ``import-linter`` enforces.

A caller asks for capabilities, never for a provider::

    from docdoc.ingest import CapabilityRequest, parse

    document = parse(
        pdf_bytes,
        require=CapabilityRequest(media_type="application/pdf", geometry=True),
    )
"""

from __future__ import annotations

from docdoc.ingest.capabilities import CapabilityRequest, ParserCapabilities
from docdoc.ingest.errors import (
    IngestError,
    ParserCapabilityError,
    ParserError,
    ProviderError,
    UnsupportedDocumentError,
)
from docdoc.ingest.options import TransportSettings
from docdoc.ingest.parse import parse
from docdoc.ingest.parser import Parser
from docdoc.ingest.source import Limits, SourceFile

__all__ = [
    "CapabilityRequest",
    "IngestError",
    "Limits",
    "Parser",
    "ParserCapabilities",
    "ParserCapabilityError",
    "ParserError",
    "ProviderError",
    "SourceFile",
    "TransportSettings",
    "UnsupportedDocumentError",
    "parse",
]
