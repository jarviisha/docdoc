"""docdoc kernel — the canonical Document IR and its deterministic operations.

This module is the public contract (see
``specs/001-kernel-document-ir/contracts/kernel-api.md``). Anything not exported
here is private and may change without notice.

The kernel is deliberately small, immutable, and dependency-light: its only
runtime dependency is ``pydantic``. It performs no I/O, reads no clock, and uses
no randomness, so identical inputs always produce identical outputs.
"""

from __future__ import annotations

from docdoc.kernel.blob import BlobRef
from docdoc.kernel.block import Block, BlockKind
from docdoc.kernel.document import Document
from docdoc.kernel.errors import (
    CapabilityError,
    DocdocError,
    DocumentInvariantError,
    GeometryError,
    IdentityError,
    KernelError,
    MergeError,
    SpanError,
)
from docdoc.kernel.geometry import BBox, Geometry
from docdoc.kernel.identity import (
    blob_id_for,
    canonical_json,
    document_id_for,
    options_hash_for,
)
from docdoc.kernel.page import Page
from docdoc.kernel.provenance import (
    Capabilities,
    IngestProvenance,
    PageTextVerdict,
    TextLayerRecord,
)
from docdoc.kernel.span import Span
from docdoc.kernel.span_index import SpanIndex
from docdoc.kernel.table import Table, TableCell
from docdoc.kernel.token import Token

__all__ = [
    "BBox",
    "BlobRef",
    "Block",
    "BlockKind",
    "Capabilities",
    "CapabilityError",
    "DocdocError",
    "Document",
    "DocumentInvariantError",
    "Geometry",
    "GeometryError",
    "IdentityError",
    "IngestProvenance",
    "KernelError",
    "MergeError",
    "Page",
    "PageTextVerdict",
    "Span",
    "SpanError",
    "SpanIndex",
    "Table",
    "TableCell",
    "TextLayerRecord",
    "Token",
    "blob_id_for",
    "canonical_json",
    "document_id_for",
    "options_hash_for",
]
