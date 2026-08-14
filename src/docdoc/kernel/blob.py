"""Reference to the original source file.

A ``BlobRef`` carries the source's *identity*, never its bytes (FR-003). Bytes
live in an artifact store, which is an outer-layer concern the kernel knows
nothing about.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

__all__ = ["BlobRef"]


class BlobRef(BaseModel):
    """Identity and metadata of the file a document was produced from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    blob_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    mime_type: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    filename: str | None = None
