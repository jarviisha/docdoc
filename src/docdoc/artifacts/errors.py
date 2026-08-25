"""Errors raised by the artifact store.

``ArtifactError`` is one of the two names the constitution's error model lists
that no code defined until Milestone 7. Like every other docdoc error it carries
structured attributes rather than only a message, so the HTTP layer can map it to
a status without parsing prose.

**It carries no payload and no document content.** A store that quoted the
artifact it choked on would put extracted values into a message that travels
into logs and error bodies, which FR-043 forbids. Identities and reasons only.
"""

from __future__ import annotations

from docdoc.kernel.errors import DocdocError

__all__ = ["ArtifactError"]


class ArtifactError(DocdocError):
    """A stored artifact could not be trusted, or a write would have destroyed one.

    Two situations reach here, and they are different in kind:

    ``integrity``
        The stored payload does not match the ``content_id`` recorded beside it.
        The bytes on disk are not what was written. Recomputing over this would
        hide a failing disk behind a slightly slower run, so it raises.

    ``conflict``
        A write arrived for an identity already present, carrying different
        content. Under ADR-0003 an identity covers every result-affecting input,
        so two disagreeing results under one identity mean either corruption or a
        processor whose output moved while its version did not — the failure
        ADR-0003 assigns to human review because the system cannot generally
        detect it. This is the one place it can, and it refuses to overwrite.
    """

    def __init__(
        self,
        message: str,
        *,
        reason: str,
        artifact_id: str | None = None,
        root: str | None = None,
        stage: str | None = None,
    ) -> None:
        super().__init__(message)
        self.reason = reason
        self.artifact_id = artifact_id
        self.root = root
        self.stage = stage
