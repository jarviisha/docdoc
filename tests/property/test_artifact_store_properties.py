"""The one property the store must have: what goes in comes back.

Everything else this milestone builds rests on it. A store that round-trips
*almost* exactly is worse than no store, because the difference appears as a
result that is subtly wrong and carries every mark of being right.

Generated payloads rather than fixtures, because the failure mode being hunted is
a value the canonical encoding handles differently from Python — a float that
does not round-trip, a key order that moves a hash, a nested empty container.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

from docdoc.artifacts import FileArtifactStore, content_id_of


class _Payload(BaseModel):
    text: str
    number: int
    ratio: float
    flag: bool
    items: tuple[str, ...]
    nested: dict[str, int]


_payloads = st.builds(
    _Payload,
    text=st.text(max_size=200),
    number=st.integers(min_value=-(2**40), max_value=2**40),
    ratio=st.floats(allow_nan=False, allow_infinity=False, width=64),
    flag=st.booleans(),
    items=st.lists(st.text(max_size=40), max_size=8).map(tuple),
    nested=st.dictionaries(st.text(max_size=20), st.integers(), max_size=8),
)

_ids = st.integers(min_value=0, max_value=2**32).map(lambda n: f"sha256:{n:064x}")


def _put(store: FileArtifactStore, artifact_id: str, payload: _Payload) -> None:
    store.put(
        artifact_id,
        payload,
        stage="extract",
        input_artifact_id=None,
        processor_id="extractor",
        processor_version="1.0.0",
        options_hash="sha256:" + "d" * 64,
        artifact_format_version=1,
    )


@given(artifact_id=_ids, payload=_payloads)
def test_put_then_get_returns_an_equal_model(artifact_id: str, payload: _Payload) -> None:
    with tempfile.TemporaryDirectory() as root:
        store = FileArtifactStore(Path(root))
        _put(store, artifact_id, payload)
        assert store.get(artifact_id, model=_Payload, artifact_format_version=1) == payload


@given(payload=_payloads)
def test_the_content_id_is_stable_across_serialisations(payload: _Payload) -> None:
    """Two encodings of one model hash the same, or reuse is a coin flip."""
    once = content_id_of(json.loads(payload.model_dump_json()))
    twice = content_id_of(json.loads(payload.model_dump_json()))
    assert once == twice


@given(artifact_id=_ids, payload=_payloads)
def test_writing_the_same_payload_twice_never_conflicts(
    artifact_id: str, payload: _Payload
) -> None:
    """Idempotence is not approximate.

    If a re-encoding of an identical model ever hashed differently, every
    ``--verify-cache`` run would raise on healthy data.
    """
    with tempfile.TemporaryDirectory() as root:
        store = FileArtifactStore(Path(root))
        _put(store, artifact_id, payload)
        _put(store, artifact_id, payload)
        assert store.get(artifact_id, model=_Payload, artifact_format_version=1) == payload
