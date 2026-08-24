"""The store: append-only, content-addressed, and ignorant of what it holds.

Two implementations. :class:`NullArtifactStore` misses on every read and drops
every write, and is the default — which is what makes "the store is optional"
true by construction rather than by a flag being checked correctly at every call
site. :class:`FileArtifactStore` is the real one.

**There is no default root.** A store exists because an operator asked for one
and said where. The artifacts hold extracted values and the blobs hold whole
source documents, so a default location would be docdoc choosing where a
customer's documents accumulate.

**Four read outcomes, and only one is an error** (ADR-0010 §4). Absent is a miss.
An incompatible format version is a miss, logged — a version bump is an expected
event on upgrade, and making it fatal would break every run until somebody
cleared a directory by hand. A payload that does not match its ``content_id``
**raises**, because that is corruption and recomputing over it hides a failing
disk behind a slower run. And a store that cannot be reached at all degrades: the
run proceeds without reuse.

**A write never overwrites.** Identical content is a no-op; divergent content
raises. This is the one place the system can see a processor whose output moved
while its version did not — the failure ADR-0003 hands to human review because it
is generally undetectable. Here the evidence exists for exactly one moment, and
this refuses to discard it.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from docdoc.artifacts.envelope import ArtifactEnvelope, content_id_of
from docdoc.artifacts.errors import ArtifactError

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "ArtifactStore",
    "FileArtifactStore",
    "NullArtifactStore",
]

_logger = logging.getLogger("docdoc.artifacts")

M = TypeVar("M", bound=BaseModel)


class _Write(Enum):
    """What happened when the store tried to create an entry."""

    CREATED = "created"
    #: Somebody else got there first. Whether that is fine or a conflict depends
    #: on what they wrote, so the caller has to look.
    EXISTS = "exists"
    #: The store could not be written to at all. Degrade, do not raise (FR-063).
    FAILED = "failed"


#: Owner-only, on both the directories and the files. FR-044: these hold
#: extracted values, and the blobs beside them hold whole documents.
_DIR_MODE = 0o700
_FILE_MODE = 0o600


class ArtifactStore(Protocol):
    """What the pipeline needs from a store.

    Generic over the stored model: the caller names it. That is what keeps this
    layer from importing ``ExtractionResult`` and, through it, four layers it has
    no business knowing about.
    """

    def get(
        self,
        artifact_id: str,
        *,
        model: type[M],
        artifact_format_version: int,
    ) -> M | None:
        """The stored result, or ``None`` on any kind of miss."""
        ...

    def put(
        self,
        artifact_id: str,
        payload: BaseModel,
        *,
        stage: str,
        input_artifact_id: str | None,
        processor_id: str,
        processor_version: str,
        options_hash: str,
        artifact_format_version: int,
    ) -> None:
        """Store a result. A no-op if an identical one is already there."""
        ...

    def envelope(self, artifact_id: str) -> ArtifactEnvelope | None:
        """The raw envelope, for explaining a derivation."""
        ...

    def clear(self, *, stage: str | None = None) -> int:
        """Remove everything, or one stage. Returns how many were removed."""
        ...


class NullArtifactStore:
    """A store that stores nothing, and is the default.

    Not a mock and not a test double: it is the configuration in which docdoc
    runs when nobody asked for a store, and every result must be identical to one
    produced with a store present.
    """

    def get(
        self,
        artifact_id: str,
        *,
        model: type[M],
        artifact_format_version: int,
    ) -> M | None:
        return None

    def put(
        self,
        artifact_id: str,
        payload: BaseModel,
        *,
        stage: str,
        input_artifact_id: str | None,
        processor_id: str,
        processor_version: str,
        options_hash: str,
        artifact_format_version: int,
    ) -> None:
        return None

    def envelope(self, artifact_id: str) -> ArtifactEnvelope | None:
        return None

    def clear(self, *, stage: str | None = None) -> int:
        return 0


class FileArtifactStore:
    """A filesystem store, laid out as ADR-0010 §1 describes.

    ``<root>/artifacts/<aa>/<full-hash>.json``, two-character fan-out because a
    flat directory of a hundred thousand entries is slow on several filesystems
    and free to avoid.
    """

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self._artifacts = self.root / "artifacts"

    # -- layout ---------------------------------------------------------------

    def _path_for(self, artifact_id: str) -> Path:
        digest = _digest_of(artifact_id)
        return self._artifacts / digest[:2] / f"{digest}.json"

    def _all_paths(self) -> Iterator[Path]:
        if not self._artifacts.is_dir():
            return
        yield from sorted(self._artifacts.glob("*/*.json"))

    # -- reading --------------------------------------------------------------

    def get(
        self,
        artifact_id: str,
        *,
        model: type[M],
        artifact_format_version: int,
    ) -> M | None:
        stored = self.envelope(artifact_id)
        if stored is None:
            return None

        if stored.artifact_format_version != artifact_format_version:
            # A miss, deliberately, and logged so the cost is explicable. See the
            # module docstring: a fatal version mismatch means an upgrade breaks
            # every run until a directory is cleared by hand.
            _logger.info(
                "artifact format mismatch, recomputing",
                extra={
                    "event": "artifacts.format_mismatch",
                    "artifact_id": artifact_id,
                    "stored_version": stored.artifact_format_version,
                    "expected_version": artifact_format_version,
                },
            )
            return None

        try:
            return model.model_validate(stored.payload)
        except ValidationError as error:
            # The format version said this payload fits the current model and it
            # does not. That is a version somebody forgot to move, not a
            # recoverable miss, and returning None here would bury it.
            raise ArtifactError(
                f"stored artifact does not fit {model.__name__} although its format "
                f"version claims to; the version was probably not bumped",
                reason="model_mismatch",
                artifact_id=artifact_id,
                root=str(self.root),
                stage=stored.stage,
            ) from error

    def envelope(self, artifact_id: str) -> ArtifactEnvelope | None:
        """Read and verify one envelope.

        Verification happens here rather than in ``get`` so that reading an
        envelope for any reason — including explaining a derivation — cannot
        return one whose bytes are not what was written.
        """
        path = self._path_for(artifact_id)
        try:
            raw = path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as error:
            # Unreadable is not the same as absent, but for a *cache* it has the
            # same correct response: proceed without it. FR-063.
            _logger.warning(
                "artifact store unreadable, continuing without reuse",
                extra={
                    "event": "artifacts.unreadable",
                    "artifact_id": artifact_id,
                    "error": type(error).__name__,
                },
            )
            return None

        try:
            stored = ArtifactEnvelope.model_validate_json(raw)
        except ValidationError as error:
            raise ArtifactError(
                "stored artifact is not a readable envelope",
                reason="integrity",
                artifact_id=artifact_id,
                root=str(self.root),
            ) from error

        if not stored.content_matches():
            raise ArtifactError(
                "stored artifact does not match the content id recorded beside it; "
                "the bytes on disk are not what was written",
                reason="integrity",
                artifact_id=artifact_id,
                root=str(self.root),
                stage=stored.stage,
            )

        if stored.artifact_id != artifact_id:
            raise ArtifactError(
                "stored artifact carries a different identity than the one it is "
                "filed under",
                reason="integrity",
                artifact_id=artifact_id,
                root=str(self.root),
                stage=stored.stage,
            )
        return stored

    # -- writing --------------------------------------------------------------

    def put(
        self,
        artifact_id: str,
        payload: BaseModel,
        *,
        stage: str,
        input_artifact_id: str | None,
        processor_id: str,
        processor_version: str,
        options_hash: str,
        artifact_format_version: int,
    ) -> None:
        serialised: dict[str, Any] = json.loads(payload.model_dump_json())

        existing = self.envelope(artifact_id)
        if existing is not None:
            if existing.content_id == content_id_of(serialised):
                return  # Already there, byte for byte. Nothing to do.
            raise ArtifactError(
                "a different result is already stored under this identity; an "
                "identity covers every result-affecting input, so this means "
                "either corruption or a processor whose output moved without its "
                "version moving (ADR-0003)",
                reason="conflict",
                artifact_id=artifact_id,
                root=str(self.root),
                stage=stage,
            )

        envelope = ArtifactEnvelope.build(
            artifact_id=artifact_id,
            stage=stage,
            input_artifact_id=input_artifact_id,
            processor_id=processor_id,
            processor_version=processor_version,
            options_hash=options_hash,
            artifact_format_version=artifact_format_version,
            payload=serialised,
        )

        outcome = self._create_exclusively(
            self._path_for(artifact_id), envelope.model_dump_json(indent=2)
        )
        if outcome is not _Write.EXISTS:
            return

        # Lost a race. The check above passed because the winner had not landed
        # yet, so the comparison has to happen again against what is actually
        # there -- otherwise a divergent concurrent write is accepted in silence,
        # which is the exact failure the conflict rule exists to prevent.
        landed = self.envelope(artifact_id)
        if landed is not None and landed.content_id == envelope.content_id:
            return
        raise ArtifactError(
            "a different result was stored under this identity concurrently; an "
            "identity covers every result-affecting input, so this means either "
            "corruption or a processor whose output moved without its version "
            "moving (ADR-0003)",
            reason="conflict",
            artifact_id=artifact_id,
            root=str(self.root),
            stage=stage,
        )

    def _create_exclusively(self, path: Path, text: str) -> _Write:
        """Write a temporary file, then link it into place, failing if taken.

        Two properties are needed at once and only this pair gives both.
        ``os.link`` is atomic **and** refuses an existing target, so a racing
        writer is told rather than silently overwritten; writing the content to a
        temporary file first means the linked-in file is complete the instant it
        is visible, so no reader can see a half-written artifact (FR-016).

        ``os.replace`` was the obvious choice and is wrong here: it is atomic but
        it overwrites, which turns the conflict rule into a check-then-write race
        that a concurrency test caught immediately.

        A store that cannot be written to degrades rather than failing the run
        (FR-063): the result is already computed and correct, and losing a cache
        entry is not a reason to lose it.
        """
        temporary: str | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True, mode=_DIR_MODE)
            handle, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(text)
            os.chmod(temporary, _FILE_MODE)
            os.link(temporary, path)
        except FileExistsError:
            return _Write.EXISTS
        except OSError as error:
            _logger.warning(
                "artifact store unwritable, continuing without storing",
                extra={
                    "event": "artifacts.unwritable",
                    "path": str(path),
                    "error": type(error).__name__,
                },
            )
            return _Write.FAILED
        finally:
            if temporary is not None:
                Path(temporary).unlink(missing_ok=True)
        return _Write.CREATED

    # -- clearing -------------------------------------------------------------

    def clear(self, *, stage: str | None = None) -> int:
        """All of it, or one stage. Two subsets and no query language (FR-019).

        This is the supported recovery path from a failed integrity check: a
        human clears and recomputes, rather than the run that found the fault
        silently overwriting the evidence of it.
        """
        removed = 0
        for path in self._all_paths():
            if stage is not None and _stage_of(path) != stage:
                continue
            path.unlink(missing_ok=True)
            removed += 1
        return removed


def _stage_of(path: Path) -> str | None:
    """The stage recorded in an envelope, read without verifying it.

    ``clear`` must work on a store containing a corrupt entry — that is most of
    what it is for — so this deliberately does not go through ``envelope()``.
    """
    try:
        return str(json.loads(path.read_text(encoding="utf-8")).get("stage"))
    except (OSError, ValueError):
        return None


def _digest_of(artifact_id: str) -> str:
    """The bare hex digest of a ``sha256:``-prefixed identity.

    Used as a filename, so a value carrying a path separator or a parent
    reference is refused rather than escaping the store's root.
    """
    digest = artifact_id.split(":", 1)[-1]
    if not digest or not all(character in "0123456789abcdef" for character in digest):
        raise ArtifactError(
            f"{artifact_id!r} is not a content-addressed identity",
            reason="malformed_id",
            artifact_id=artifact_id,
        )
    return digest
