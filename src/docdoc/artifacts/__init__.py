"""The content-addressed store that knows nothing about what it stores.

ADR-0003 defines an artifact chain in which every stage's output is addressed by
a hash of that stage's *inputs*. This layer holds one end of that: somewhere to
put an artifact, and a way to get it back and know it is the same one.

**Why this is a layer and not a module inside the pipeline.** Deserialising a
stored result needs the model class. A store that imported ``ExtractionResult``
would depend on four layers, and every stage anyone adds later would widen it.
So the caller names the model at the call site, and this package's entire
dependency set is ``pydantic`` plus two kernel helpers. That is not a style
preference -- it is the reason a machine can check the boundary, and this
repository's layer chain has stayed true for six milestones precisely because a
machine checks it.

**What it must never import:** ``docdoc.ingest``, ``docdoc.extraction``,
``docdoc.grounding``, ``docdoc.validation``, ``docdoc.evaluation``,
``docdoc.pipeline``, or any provider SDK. It sits directly above the kernel and
depends on nothing else.

**The two hashes, which are easy to confuse and must not be.** An
``artifact_id`` hashes a stage's inputs; it says *which* result this is. A
``content_id`` hashes the stored payload; it says the bytes are intact.
Rehashing a payload and comparing it to the artifact id would always fail, so a
store carrying only the artifact id cannot detect corruption at all.
"""

from __future__ import annotations

from docdoc.artifacts.blobs import BlobStore
from docdoc.artifacts.derivation import DerivationRecord, derivation_chain, derivation_of
from docdoc.artifacts.envelope import ArtifactEnvelope, content_id_of
from docdoc.artifacts.errors import ArtifactError
from docdoc.artifacts.store import ArtifactStore, FileArtifactStore, NullArtifactStore

__all__ = [
    "ArtifactEnvelope",
    "ArtifactError",
    "ArtifactStore",
    "BlobStore",
    "DerivationRecord",
    "FileArtifactStore",
    "NullArtifactStore",
    "content_id_of",
    "derivation_chain",
    "derivation_of",
]
