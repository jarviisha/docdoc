"""Each page's clockwise viewer turn, kept beside the cached parse (020).

PaddleX straightens a turned scan before reading it, so its boxes are in the
straightened frame. The turn it applied never enters the `Document` -- that
would change the bytes and identity of every stored parse -- so it is stored as
a small sidecar artifact under an id derived from the parse's own
`document_id`. Whatever finds the `Document` finds its angles, a cache hit
included, and retention deletes the sidecar with its parse.

A missing sidecar means "unknown": a parse from before this change, a parser
that reports no orientation, or a lost write. Never "upright".
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, field_validator

from docdoc.kernel.identity import canonical_json, content_id_for

if TYPE_CHECKING:
    from docdoc.artifacts.store import ArtifactStore

__all__ = [
    "ORIENTATION_FORMAT_VERSION",
    "PageOrientation",
    "orientation_id_for",
    "read_orientation",
]

_logger = logging.getLogger("docdoc.pipeline")

ORIENTATION_FORMAT_VERSION = 1


class PageOrientation(BaseModel):
    """One clockwise turn per page, in `Document.pages` order."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    angles: tuple[int, ...]

    @field_validator("angles")
    @classmethod
    def _quarter_turns_only(cls, value: tuple[int, ...]) -> tuple[int, ...]:
        if any(angle not in (0, 90, 180, 270) for angle in value):
            raise ValueError(f"page angles must each be 0, 90, 180 or 270, got {value!r}")
        return value


def orientation_id_for(document_id: str) -> str:
    """The sidecar's id: derived from `document_id` under its own domain tag.

    Hashing a JSON object rather than the id alone keeps it apart from every
    real artifact id, which hashes either raw bytes or a differently shaped
    object.
    """
    return content_id_for(canonical_json({"v": 1, "orientation_of": document_id}))


def read_orientation(store: ArtifactStore, document_id: str) -> tuple[int, ...] | None:
    """The stored angles, or `None` when absent or the store cannot say."""
    try:
        found = store.get(
            orientation_id_for(document_id),
            model=PageOrientation,
            artifact_format_version=ORIENTATION_FORMAT_VERSION,
        )
    except Exception as error:
        # Display-only data: an unreadable sidecar is "unknown", never a failed read
        # of the result it sits beside.
        _logger.warning(
            "page orientation unreadable",
            extra={"event": "pipeline.orientation_unreadable", "error": type(error).__name__},
        )
        return None
    return None if found is None else found.angles
