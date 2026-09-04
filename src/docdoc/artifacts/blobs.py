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
from docdoc.artifacts.paths import (
    DEFAULT_TENANT,
    DegradationLog,
    secure_mkdir,
    tenant_root,
)
from docdoc.artifacts.paths import FILE_MODE as _FILE_MODE
from docdoc.kernel import blob_id_for

__all__ = ["BlobStore"]

_logger = logging.getLogger("docdoc.artifacts")


class BlobStore:
    """Source bytes on a filesystem, keyed by their own content.

    Namespaced per tenant above the fan-out, with the **default tenant keeping
    the unprefixed layout** so an existing deployment's blobs stay where they
    are (FR-084a). See ``paths.tenant_root``.
    """

    def __init__(self, root: str | Path, *, tenant_id: str = DEFAULT_TENANT) -> None:
        self.root = Path(root)
        segment = tenant_root(tenant_id)
        self._base = self.root / segment if segment else self.root
        self._blobs = self._base / "blobs"
        #: Once per condition, not once per lookup. See `DegradationLog`.
        self._degradations = DegradationLog()

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
        """The stored bytes, ``None`` if absent, and it **raises** if unreadable.

        Three answers rather than two, matching ``S3BlobStore.get``. A missing
        file means this deployment does not have the document; an ``OSError``
        means the mount is gone or the permissions changed, and a caller told
        ``None`` for that concludes the document does not exist. In the worker
        that conclusion is terminal and irreversible, which is far too strong a
        thing to infer from a disk that went away for a moment.
        """
        try:
            return self._path_for(blob_id).read_bytes()
        except FileNotFoundError:
            return None
        except OSError as error:
            if self._degradations.first_time("unreadable"):
                _logger.warning(
                    "blob store unreadable",
                    extra={
                        "event": "artifacts.blob_unreadable",
                        "blob_id": blob_id,
                        "error": type(error).__name__,
                    },
                )
            raise ArtifactError(
                "the blob store could not be read; this is not the same as the "
                "document being absent, and the caller must not treat it as such",
                reason="unavailable",
                artifact_id=blob_id,
                root=str(self.root),
            ) from error

    def size_of(self, blob_id: str) -> int | None:
        """The stored size in bytes, for metadata without reading the document."""
        try:
            return self._path_for(blob_id).stat().st_size
        except (FileNotFoundError, OSError):
            return None

    def probe(self) -> None:
        """Reach the store and return, or raise. What readiness asks (FR-054).

        A separate method rather than a call to ``size_of`` because that one
        cannot answer this question: it swallows ``OSError`` and returns ``None``
        for an unreadable store as well as an absent blob, which is the correct
        behaviour for reuse — ADR-0010 §4's "run without reuse rather than fail"
        — and useless as a probe, since every answer is the same answer.

        Statting the root rather than reading a fixed key: a filesystem store's
        root is what can vanish under it, and a missing key proves nothing about
        a mount that is no longer there.

        Reads no document and creates nothing, so a probe has no side effect and
        costs no provider call (FR-056).
        """
        os.stat(self.root)
