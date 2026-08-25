"""The envelope, and the reason it carries two hashes instead of one.

The claim under test is narrow and load-bearing: an ``artifact_id`` cannot detect
a corrupted payload, and a store that carried only the artifact id would have no
way to tell a good artifact from a damaged one.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

from docdoc.artifacts import ArtifactEnvelope, content_id_of


class _Payload(BaseModel):
    value: str
    count: int = 0


def _envelope(**overrides: object) -> ArtifactEnvelope:
    fields: dict[str, object] = {
        "artifact_id": "sha256:" + "a" * 64,
        "stage": "extract",
        "input_artifact_id": "sha256:" + "b" * 64,
        "processor_id": "extractor",
        "processor_version": "1.0.0",
        "options_hash": "sha256:" + "c" * 64,
        "artifact_format_version": 1,
        "payload": {"value": "INV-001", "count": 3},
    }
    fields.update(overrides)
    return ArtifactEnvelope.build(**fields)  # type: ignore[arg-type]


def test_build_derives_the_content_id_from_the_payload() -> None:
    envelope = _envelope()
    assert envelope.content_id == content_id_of(envelope.payload)
    assert envelope.content_matches()


def test_a_mutated_payload_no_longer_matches_its_content_id() -> None:
    envelope = _envelope()
    tampered = envelope.model_copy(update={"payload": {"value": "INV-999", "count": 3}})
    assert not tampered.content_matches()


def test_the_artifact_id_cannot_detect_the_same_mutation() -> None:
    """The point of having two hashes, stated as a test.

    An artifact id hashes a stage's *inputs*. Mutating the stored payload leaves
    it untouched, so any integrity check built on it alone reports health while
    holding damaged bytes.
    """
    envelope = _envelope()
    tampered = envelope.model_copy(update={"payload": {"value": "INV-999", "count": 3}})

    assert tampered.artifact_id == envelope.artifact_id  # unchanged, and useless here
    assert tampered.content_id != content_id_of(tampered.payload)  # this is what sees it


def test_the_content_id_is_insensitive_to_key_order() -> None:
    """Canonical encoding, so a re-serialised payload is not a false alarm."""
    one = content_id_of({"value": "INV-001", "count": 3})
    other = content_id_of({"count": 3, "value": "INV-001"})
    assert one == other


def test_a_changed_field_moves_the_content_id() -> None:
    assert content_id_of({"value": "a"}) != content_id_of({"value": "b"})


def test_the_envelope_is_frozen() -> None:
    envelope = _envelope()
    with pytest.raises(Exception, match=r"frozen|immutable"):
        envelope.payload = {}  # type: ignore[misc]


def test_reading_an_envelope_back_does_not_recompute_its_content_id() -> None:
    """The stored value is the one under test and must survive a round trip.

    Recomputing on the way in would make every stored artifact verify, which is
    the failure mode this whole mechanism exists to avoid.
    """
    envelope = _envelope()
    corrupted = envelope.model_dump()
    corrupted["payload"] = {"value": "tampered", "count": 3}

    restored = ArtifactEnvelope.model_validate(corrupted)
    assert restored.content_id == envelope.content_id
    assert not restored.content_matches()
