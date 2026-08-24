"""What one stored artifact looks like on disk.

**The two hashes, and why one of them cannot be dropped.** Under ADR-0003 an
``artifact_id`` is a hash of a stage's *inputs* — the previous artifact's id, the
processor's identity and version, and the folded options. It is not derived from
the payload at all. So the obvious integrity check, "rehash what is stored and
compare it to the id it is stored under", does not merely fail to detect
corruption: it would fail on every healthy artifact, because the two values were
never meant to be equal.

Detecting corruption needs a second hash that genuinely comes from the content.
That is ``content_id``, and the kernel already had the helper — made public at
commit ``b66f687`` because three layers needed it, and this is the fourth.

**``stage`` is a plain string here, not the pipeline's enum.** This layer sits
below the pipeline and must not import it (Principle X). The store records the
stage so that ``clear(stage=...)`` and a derivation can name it; it never
interprets it, and a store that had opinions about the stage vocabulary would be
a store that knew what it was storing.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from docdoc.kernel import canonical_json, content_id_for

__all__ = ["ArtifactEnvelope", "content_id_of"]


def content_id_of(payload: dict[str, Any]) -> str:
    """The content id of a serialised payload, under the canonical encoding.

    One function, called on both the write and the read path, because an
    integrity check whose two sides can drift is not one.
    """
    return content_id_for(canonical_json(payload))


class ArtifactEnvelope(BaseModel):
    """One stage's stored output, with everything needed to distrust it.

    Immutable. There is no update path and no ``put`` that overwrites, which is
    what makes "append-only" a property of this store rather than a description
    of how it is usually used.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The identity this envelope is stored under. A hash of the stage's inputs.
    artifact_id: str = Field(min_length=1)

    #: Which stage produced it. Recorded for `clear` and `explain`, never used to
    #: choose a model — the caller names the model (see the package docstring).
    stage: str = Field(min_length=1)

    #: The previous link in the chain. ``None`` only for the parse stage, whose
    #: input is a blob rather than an artifact.
    input_artifact_id: str | None = None

    processor_id: str = Field(min_length=1)
    processor_version: str = Field(min_length=1)
    options_hash: str = Field(min_length=1)

    #: The stored model's shape, per ADR-0010. **Not** the docdoc release
    #: version: tying reuse to that would invalidate every artifact on every
    #: release, including releases that change a docstring.
    artifact_format_version: int = Field(ge=1)

    #: ``content_id_of(payload)``. The only field that can detect corruption.
    content_id: str = Field(min_length=1)

    payload: dict[str, Any]

    @classmethod
    def build(
        cls,
        *,
        artifact_id: str,
        stage: str,
        input_artifact_id: str | None,
        processor_id: str,
        processor_version: str,
        options_hash: str,
        artifact_format_version: int,
        payload: dict[str, Any],
    ) -> ArtifactEnvelope:
        """Build an envelope, deriving ``content_id`` from the payload.

        The normal entry point. The bare constructor stays available for reading
        an envelope back off disk, where the stored ``content_id`` is the value
        under test and must not be recomputed on the way in.
        """
        return cls(
            artifact_id=artifact_id,
            stage=stage,
            input_artifact_id=input_artifact_id,
            processor_id=processor_id,
            processor_version=processor_version,
            options_hash=options_hash,
            artifact_format_version=artifact_format_version,
            content_id=content_id_of(payload),
            payload=payload,
        )

    def content_matches(self) -> bool:
        """Whether the payload still hashes to the recorded ``content_id``."""
        return content_id_of(self.payload) == self.content_id
