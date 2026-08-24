"""Why an identity is the value it is.

ADR-0003 accepted that cache keys "cannot be computed by hand or eyeballed in
logs" on one explicit condition: that something would explain them. This is that
something. Without it the first cache-correctness incident is unarguable in both
directions — nobody can show the reuse was right, and nobody can show it was
wrong.

**It carries no content.** Not the payload, not an extracted value, not a prompt
body, not a credential. It names the *inputs* folded into an options hash, never
their values, because the values are the document (FR-025).

A derivation is read from the record a write left behind. A run with no store
configured produces identities that were never recorded, so there is nothing to
read and this says so, rather than reconstructing something plausible.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from docdoc.artifacts.store import ArtifactStore

__all__ = ["DerivationRecord", "derivation_chain", "derivation_of"]


class DerivationRecord(BaseModel):
    """One link of the chain, explained."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_id: str = Field(min_length=1)
    stage: str = Field(min_length=1)
    input_artifact_id: str | None
    processor_id: str = Field(min_length=1)
    processor_version: str = Field(min_length=1)
    options_hash: str = Field(min_length=1)

    #: The *names* of the inputs folded into ``options_hash`` — never their
    #: values. "prompt_hash" is a name; the prompt is a document.
    folded_inputs: tuple[str, ...] = ()


def derivation_of(store: ArtifactStore, artifact_id: str) -> DerivationRecord | None:
    """Explain one identity, or ``None`` if this store never recorded it."""
    envelope = store.envelope(artifact_id)
    if envelope is None:
        return None
    return DerivationRecord(
        artifact_id=envelope.artifact_id,
        stage=envelope.stage,
        input_artifact_id=envelope.input_artifact_id,
        processor_id=envelope.processor_id,
        processor_version=envelope.processor_version,
        options_hash=envelope.options_hash,
        folded_inputs=(),
    )


def derivation_chain(store: ArtifactStore, artifact_id: str) -> tuple[DerivationRecord, ...]:
    """Walk back from an identity toward the source blob.

    Stops at the first link this store does not hold — a partially cleared store
    explains what it can and stays silent about the rest, which is more useful
    than refusing to explain anything.
    """
    chain: list[DerivationRecord] = []
    seen: set[str] = set()
    current: str | None = artifact_id

    while current is not None and current not in seen:
        seen.add(current)
        record = derivation_of(store, current)
        if record is None:
            break
        chain.append(record)
        current = record.input_artifact_id

    return tuple(chain)
