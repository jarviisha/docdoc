"""Submitted source bytes, addressed by ``blob_id``.

Separate from the artifact store because a blob has no envelope, no processor, no
options hash, and no format version — it is the original file, and the original
file has no shape docdoc chose. Sharing one store would mean either an envelope
around bytes that need none, or a store with two modes.

**This is the more sensitive of the two stores.** An artifact holds extracted
values; a blob holds the whole document they were extracted from. FR-044 covers
both, and the reason it names blobs explicitly is that they are the easier of the
two to overlook.

``put`` is idempotent by construction: identical bytes hash to one ``blob_id``,
so submitting the same document twice yields one entry (FR-021).
"""

from __future__ import annotations

import logging
import os
import tempfile
from pathlib import Path

from docdoc.artifacts.errors import ArtifactError
from docdoc.artifacts.paths import FILE_MODE as _FILE_MODE
from docdoc.artifacts.paths import secure_mkdir
from docdoc.kernel import blob_id_for

__all__ = ["BlobStore"]

_logger = logging.getLogger("docdoc.artifacts")

class BlobStore:
    """Source bytes on a filesystem, keyed by their own content."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._blobs = self.root / "blobs"

    def _path_for(self, blob_id: str) -> Path:
        digest = blob_id.split(":", 1)[-1]
        if not digest or not all(character in "0123456789abcdef" for character in digest):
            raise ArtifactError(
                f"{blob_id!r} is not a content-addressed identity",
                reason="malformed_id",
                artifact_id=blob_id,
            )
        return self._blobs / digest[:2] / digest

    def put(self, data: bytes) -> str:
        """Store bytes and return their identity. Idempotent."""
        blob_id = blob_id_for(data)
        path = self._path_for(blob_id)
        if path.exists():
            # Content-addressed, so an existing entry with this name *is* these
            # bytes. There is nothing to compare and nothing to overwrite.
            return blob_id

        secure_mkdir(path.parent, below=self.root)
        handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
            os.chmod(temporary, _FILE_MODE)
            os.replace(temporary, path)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
        return blob_id

    def get(self, blob_id: str) -> bytes | None:
        """The stored bytes, or ``None`` if this deployment does not have them."""
        try:
            return self._path_for(blob_id).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            _logger.warning(
                "blob store unreadable",
                extra={
                    "event": "artifacts.blob_unreadable",
                    "blob_id": blob_id,
                    "error": type(error).__name__,
                },
            )
            return None

    def size_of(self, blob_id: str) -> int | None:
        """The stored size in bytes, for metadata without reading the document."""
        try:
            return self._path_for(blob_id).stat().st_size
        except (FileNotFoundError, OSError):
            return None
