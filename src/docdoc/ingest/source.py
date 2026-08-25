"""The file a caller hands in, and the limits it must fit inside.

Two rules shape this module:

* **The bytes decide the type.** A caller's declared media type is recorded and
  never trusted, so a PNG renamed ``.pdf`` is parsed as a PNG (ING-1, R9).
* **Limits are enforced before anything expensive.** Size and type are checked
  at construction; page count is checked as soon as it is known, which is during
  the text-layer assessment (ING-2).
"""

from __future__ import annotations

import os
from typing import Final

from pydantic import BaseModel, ConfigDict, Field

from docdoc.ingest.errors import UnsupportedDocumentError
from docdoc.kernel import BlobRef, blob_id_for

__all__ = [
    "MAX_DOCUMENT_BYTES_ENV",
    "MAX_PAGES_ENV",
    "Limits",
    "SourceFile",
    "detect_media_type",
]

PDF: Final = "application/pdf"
JPEG: Final = "image/jpeg"
PNG: Final = "image/png"
TIFF: Final = "image/tiff"

#: Byte signatures, longest first so a longer match wins. TIFF is *detected*
#: even though it is not *accepted*, so a TIFF is rejected as an unsupported
#: type rather than as an unrecognizable file -- a far more useful error, and
#: the difference costs four bytes of comparison (research.md R9).
_SIGNATURES: Final[tuple[tuple[bytes, str], ...]] = (
    (b"\x89PNG\r\n\x1a\n", PNG),
    (b"%PDF-", PDF),
    (b"\xff\xd8\xff", JPEG),
    (b"II*\x00", TIFF),
    (b"MM\x00*", TIFF),
)


def detect_media_type(data: bytes) -> str | None:
    """The media type the bytes actually are, or ``None`` if unrecognized."""
    for signature, media_type in _SIGNATURES:
        if data.startswith(signature):
            return media_type
    return None


#: The document size limit, as configuration rather than only as a constructor
#: argument. FR-039 requires "a configurable maximum document size", and until
#: Milestone 7's convergence pass it was configurable only by a caller importing
#: the library — so an operator running the command line or the service could set
#: the request cap and not this. Named in the style of every other docdoc setting
#: so there is no second vocabulary (FR-031).
MAX_DOCUMENT_BYTES_ENV = "DOCDOC_MAX_DOCUMENT_BYTES"
MAX_PAGES_ENV = "DOCDOC_MAX_PAGES"

DEFAULT_MAX_SIZE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_PAGES = 1000


def _from_env(name: str, default: int) -> int:
    """A positive integer from the environment, or the documented default.

    An unparseable or non-positive value falls back rather than raising. A typo
    in a size limit should not stop a document being processed, and the default
    is always a safe answer — it is the value the deployment had yesterday.
    """
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        parsed = int(raw)
    except ValueError:
        return default
    return parsed if parsed > 0 else default


class Limits(BaseModel):
    """What this deployment is willing to accept.

    Defaults are a starting point, tunable per deployment — by construction for a
    caller holding the library, and by ``DOCDOC_MAX_DOCUMENT_BYTES`` and
    ``DOCDOC_MAX_PAGES`` for one running the command or the service. An explicit
    argument still wins, which is the precedence every other setting uses.

    ``image/tiff`` is deliberately absent: multi-page TIFF is common, and
    supporting it would need the page-splitting semantics this milestone puts
    out of scope. A deployment that adds it accepts that only the first page is
    read -- a decision to take explicitly, not a default.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_size_bytes: int = Field(
        default_factory=lambda: _from_env(MAX_DOCUMENT_BYTES_ENV, DEFAULT_MAX_SIZE_BYTES), gt=0
    )
    max_pages: int = Field(
        default_factory=lambda: _from_env(MAX_PAGES_ENV, DEFAULT_MAX_PAGES), gt=0
    )
    allowed_media_types: frozenset[str] = frozenset({PDF, JPEG, PNG})


class SourceFile(BaseModel):
    """Bytes plus what the caller claimed, kept apart from what they are."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    data: bytes
    #: Detected from the byte signature. This is the one that decides routing.
    media_type: str = Field(min_length=1)
    #: What the caller said. Recorded for provenance, never acted on.
    declared_media_type: str | None = None
    filename: str | None = None
    blob_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")

    @classmethod
    def from_bytes(
        cls,
        data: bytes,
        *,
        declared_media_type: str | None = None,
        filename: str | None = None,
        limits: Limits | None = None,
    ) -> SourceFile:
        """Detect the type, enforce the type and size limits, and take identity.

        Raises:
            UnsupportedDocumentError: the signature is unrecognized, the type is
                not allowed, or the file is over the size limit. In every case
                before a single byte is parsed or transmitted.
        """
        limits = limits or Limits()
        blob_id = blob_id_for(data)
        media_type = detect_media_type(data)

        if media_type is None:
            raise UnsupportedDocumentError(
                "unrecognized file signature; docdoc decides the type from the bytes, "
                f"not from the declared type {declared_media_type!r}",
                reason="mime_type",
                blob_id=blob_id,
                media_type=declared_media_type,
            )
        source = cls(
            data=data,
            media_type=media_type,
            declared_media_type=declared_media_type,
            filename=filename,
            blob_id=blob_id,
        )
        source.check_limits(limits)
        return source

    def check_limits(self, limits: Limits) -> None:
        """Enforce the media-type and size limits against this file.

        Separate from construction, and idempotent, because a caller may hand
        ``parse()`` a ``SourceFile`` it built earlier — possibly under different
        limits, possibly under none it remembers. The limits given to ``parse()``
        are documented as enforced, so they are enforced there too rather than
        only wherever the object happened to be made (FR-028).

        Raises:
            UnsupportedDocumentError: the type is not accepted, or the file is
                over the size limit. Both before a byte is parsed or transmitted.
        """
        if self.media_type not in limits.allowed_media_types:
            raise UnsupportedDocumentError(
                f"{self.media_type} is not an accepted media type; accepted: "
                f"{sorted(limits.allowed_media_types)}",
                reason="mime_type",
                blob_id=self.blob_id,
                media_type=self.media_type,
            )
        if self.size_bytes > limits.max_size_bytes:
            raise UnsupportedDocumentError(
                f"file is {self.size_bytes} bytes, over the {limits.max_size_bytes}-byte limit",
                reason="size_limit",
                blob_id=self.blob_id,
                media_type=self.media_type,
            )

    @property
    def size_bytes(self) -> int:
        return len(self.data)

    @property
    def is_pdf(self) -> bool:
        return self.media_type == PDF

    def blob_ref(self) -> BlobRef:
        """The reference a Document carries in place of the bytes (FR-024)."""
        return BlobRef(
            blob_id=self.blob_id,
            mime_type=self.media_type,
            size_bytes=self.size_bytes,
            filename=self.filename,
        )

    def check_page_count(self, pages: int, limits: Limits) -> None:
        """Enforce the page limit once the page count is known (ING-2)."""
        if pages > limits.max_pages:
            raise UnsupportedDocumentError(
                f"document has {pages} pages, over the {limits.max_pages}-page limit",
                reason="page_limit",
                blob_id=self.blob_id,
                media_type=self.media_type,
            )
