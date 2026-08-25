"""Every row of the store's semantics table (contracts/pipeline-api.md §6).

The table is short and each row is a decision that could plausibly have gone the
other way, so each gets a test that would fail if it had.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest
from pydantic import BaseModel

from docdoc.artifacts import ArtifactError, FileArtifactStore, NullArtifactStore

#: FR-044's guarantee — "readable only by the account that owns them" — is
#: expressed in POSIX mode bits, which Windows does not have; there, the same
#: property is an ACL and `artifacts/paths.py` does not write one. The honest
#: statement is therefore that **the guarantee is POSIX-only**, which matches
#: `plan.md`'s stated target platform of Linux and macOS.
#:
#: Skipped rather than silently weakened, and skipped rather than deleted: a
#: deployment on Windows does not get FR-044, and a reader of this file should be
#: told that by the skip reason rather than discover it from a store that turned
#: out to be world-readable. Implementing the ACL is a real piece of work and is
#: not this milestone's (Principle XI).
posix_permissions = pytest.mark.skipif(
    os.name != "posix",
    reason="FR-044's owner-only guarantee is POSIX mode bits; Windows would need "
    "an ACL, which paths.py does not write. Target platform is Linux and macOS.",
)

ID = "sha256:" + "a" * 64
OTHER_ID = "sha256:" + "b" * 64
INPUT_ID = "sha256:" + "c" * 64


class _Result(BaseModel):
    value: str
    count: int = 0


class _WiderResult(BaseModel):
    value: str
    count: int = 0
    required_later: str


def _store(tmp_path: Path) -> FileArtifactStore:
    return FileArtifactStore(tmp_path / "store")


def _put(
    store: FileArtifactStore, payload: _Result, *, artifact_id: str = ID, **kw: object
) -> None:
    fields: dict[str, object] = {
        "stage": "extract",
        "input_artifact_id": INPUT_ID,
        "processor_id": "extractor",
        "processor_version": "1.0.0",
        "options_hash": "sha256:" + "d" * 64,
        "artifact_format_version": 1,
    }
    fields.update(kw)
    store.put(artifact_id, payload, **fields)  # type: ignore[arg-type]


def _stored_path(store: FileArtifactStore, artifact_id: str = ID) -> Path:
    digest = artifact_id.split(":", 1)[-1]
    return store.root / "artifacts" / digest[:2] / f"{digest}.json"


# -- row 1: nothing stored --------------------------------------------------


def test_an_absent_artifact_is_a_miss(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.get(ID, model=_Result, artifact_format_version=1) is None


def test_a_stored_artifact_round_trips(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001", count=3))
    assert store.get(ID, model=_Result, artifact_format_version=1) == _Result(
        value="INV-001", count=3
    )


# -- row 2: incompatible format version -------------------------------------


def test_an_incompatible_format_version_is_a_miss_not_an_error(tmp_path: Path) -> None:
    """A version bump is an expected event on upgrade.

    Making it fatal would mean every run fails after an upgrade until somebody
    clears a directory by hand.
    """
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001"), artifact_format_version=1)
    assert store.get(ID, model=_Result, artifact_format_version=2) is None


def test_the_format_mismatch_is_logged(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001"), artifact_format_version=1)
    with caplog.at_level("INFO", logger="docdoc.artifacts"):
        store.get(ID, model=_Result, artifact_format_version=2)
    assert any(r.__dict__.get("event") == "artifacts.format_mismatch" for r in caplog.records)


def test_a_payload_that_does_not_fit_the_model_raises_rather_than_missing(
    tmp_path: Path,
) -> None:
    """A version that was not bumped is a defect, not a cache miss.

    Returning None here would bury the exact mistake the format version exists
    to catch: the stored shape and the current model disagree while the version
    claims they agree.
    """
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001"), artifact_format_version=1)
    with pytest.raises(ArtifactError) as raised:
        store.get(ID, model=_WiderResult, artifact_format_version=1)
    assert raised.value.reason == "model_mismatch"


# -- row 3: content does not match ------------------------------------------


def test_a_corrupted_payload_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001"))

    path = _stored_path(store)
    envelope = json.loads(path.read_text())
    envelope["payload"]["value"] = "INV-999"
    path.write_text(json.dumps(envelope))

    with pytest.raises(ArtifactError) as raised:
        store.get(ID, model=_Result, artifact_format_version=1)
    assert raised.value.reason == "integrity"
    assert raised.value.artifact_id == ID


def test_an_envelope_filed_under_a_different_identity_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001"))

    path = _stored_path(store)
    envelope = json.loads(path.read_text())
    envelope["artifact_id"] = OTHER_ID
    path.write_text(json.dumps(envelope))

    with pytest.raises(ArtifactError, match="different identity"):
        store.get(ID, model=_Result, artifact_format_version=1)


def test_an_unparseable_envelope_raises(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001"))
    _stored_path(store).write_text("this is not an envelope")

    with pytest.raises(ArtifactError) as raised:
        store.get(ID, model=_Result, artifact_format_version=1)
    assert raised.value.reason == "integrity"


def test_a_corrupted_artifact_is_not_recomputed_over(tmp_path: Path) -> None:
    """The store must not repair itself.

    Overwriting the damaged entry would destroy the evidence of a fault in a
    store whose whole value is that a stored result can be trusted.
    """
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001"))
    path = _stored_path(store)
    envelope = json.loads(path.read_text())
    envelope["payload"]["value"] = "INV-999"
    path.write_text(json.dumps(envelope))
    before = path.read_text()

    with pytest.raises(ArtifactError):
        store.get(ID, model=_Result, artifact_format_version=1)
    assert path.read_text() == before


# -- rows 4-6: writing ------------------------------------------------------


def test_writing_identical_content_twice_is_a_no_op(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001", count=3))
    before = _stored_path(store).read_text()
    _put(store, _Result(value="INV-001", count=3))
    assert _stored_path(store).read_text() == before


def test_writing_divergent_content_under_one_identity_raises(tmp_path: Path) -> None:
    """The one symptom available for an unbumped processor version.

    An identity covers every result-affecting input, so two disagreeing results
    under one identity mean something the system otherwise cannot see.
    """
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001"))
    with pytest.raises(ArtifactError) as raised:
        _put(store, _Result(value="INV-999"))
    assert raised.value.reason == "conflict"


def test_a_conflicting_write_never_overwrites(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001"))
    with pytest.raises(ArtifactError):
        _put(store, _Result(value="INV-999"))
    assert store.get(ID, model=_Result, artifact_format_version=1) == _Result(value="INV-001")


# -- row 7-8: degradation ---------------------------------------------------


@posix_permissions
def test_an_unwritable_root_does_not_raise(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """A failed write must not fail a run whose stages succeeded (FR-063).

    The result is already computed and correct; losing a cache entry is not a
    reason to lose it.
    """
    root = tmp_path / "store"
    (root / "artifacts").mkdir(parents=True)
    os.chmod(root / "artifacts", 0o500)
    try:
        store = FileArtifactStore(root)
        with caplog.at_level("WARNING", logger="docdoc.artifacts"):
            _put(store, _Result(value="INV-001"))
        assert any(r.__dict__.get("event") == "artifacts.unwritable" for r in caplog.records)
    finally:
        os.chmod(root / "artifacts", 0o700)


def test_a_missing_root_reads_as_a_miss(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "never-created")
    assert store.get(ID, model=_Result, artifact_format_version=1) is None
    assert store.clear() == 0


# -- atomicity (FR-016) -----------------------------------------------------


def test_no_partial_file_is_left_behind(tmp_path: Path) -> None:
    """A reader sees the previous state or the complete new one, never a half file."""
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001"))
    leftovers = list((store.root / "artifacts").glob("**/*.tmp"))
    assert leftovers == []


def test_a_failed_write_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = _store(tmp_path)

    def _explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("interrupted mid-write")

    # `os.link`, not `os.replace`: the write is exclusive-create, because
    # `os.replace` is atomic but overwrites, which made the conflict rule a
    # check-then-write race. See the note in `_create_exclusively`.
    monkeypatch.setattr("docdoc.artifacts.store.os.link", _explode)
    with pytest.raises(RuntimeError):
        _put(store, _Result(value="INV-001"))
    assert list((store.root / "artifacts").glob("**/*.tmp")) == []


# -- permissions (FR-044) ---------------------------------------------------


@posix_permissions
def test_the_store_is_not_group_or_world_readable(tmp_path: Path) -> None:
    """Artifacts hold extracted values, so their directory is the owner's alone."""
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001"))

    path = _stored_path(store)
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0


# -- clear (FR-019) ---------------------------------------------------------


def test_clear_removes_everything(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _put(store, _Result(value="a"), artifact_id=ID)
    _put(store, _Result(value="b"), artifact_id=OTHER_ID)
    assert store.clear() == 2
    assert store.get(ID, model=_Result, artifact_format_version=1) is None


def test_clear_removes_one_stage_and_leaves_the_others(tmp_path: Path) -> None:
    store = _store(tmp_path)
    _put(store, _Result(value="a"), artifact_id=ID, stage="parse")
    _put(store, _Result(value="b"), artifact_id=OTHER_ID, stage="extract")

    assert store.clear(stage="parse") == 1
    assert store.get(ID, model=_Result, artifact_format_version=1) is None
    assert store.get(OTHER_ID, model=_Result, artifact_format_version=1) is not None


def test_clear_works_on_a_store_containing_a_corrupt_entry(tmp_path: Path) -> None:
    """Most of what clear is for: recovering from the entry that raises."""
    store = _store(tmp_path)
    _put(store, _Result(value="INV-001"))
    _stored_path(store).write_text("garbage")
    assert store.clear() == 1


# -- identities -------------------------------------------------------------


@pytest.mark.parametrize(
    "bad_id",
    ["sha256:../../etc/passwd", "sha256:", "not-an-id", "sha256:zzzz"],
)
def test_a_malformed_identity_is_refused(tmp_path: Path, bad_id: str) -> None:
    """A filename built from an identity must not be able to escape the root."""
    store = _store(tmp_path)
    with pytest.raises(ArtifactError) as raised:
        store.get(bad_id, model=_Result, artifact_format_version=1)
    assert raised.value.reason == "malformed_id"


# -- the null store ---------------------------------------------------------


def test_the_null_store_misses_and_drops() -> None:
    """The default, and the reason 'the store is optional' is true by construction."""
    store = NullArtifactStore()
    store.put(
        ID,
        _Result(value="INV-001"),
        stage="extract",
        input_artifact_id=None,
        processor_id="extractor",
        processor_version="1.0.0",
        options_hash="sha256:" + "d" * 64,
        artifact_format_version=1,
    )
    assert store.get(ID, model=_Result, artifact_format_version=1) is None
    assert store.envelope(ID) is None
    assert store.clear() == 0


# -- the blob store (FR-021, FR-044) ----------------------------------------


def test_blobs_are_stored_once_per_content(tmp_path: Path) -> None:
    """FR-021 — identical bytes hash to one identity and one stored copy."""
    from docdoc.artifacts import BlobStore

    blobs = BlobStore(tmp_path)
    data = b"%PDF-1.4\nthe same document twice\n"

    first = blobs.put(data)
    second = blobs.put(data)

    assert first == second
    assert len(list((tmp_path / "blobs").glob("*/*"))) == 1
    assert blobs.get(first) == data
    assert blobs.size_of(first) == len(data)


@posix_permissions
def test_blobs_are_not_group_or_world_readable(tmp_path: Path) -> None:
    """FR-044, on the store the requirement names explicitly — and why.

    An artifact holds the values extracted from a document. A blob holds *the
    document*. FR-044 calls blobs out by name because they are the more sensitive
    of the two and the easier to overlook, and the artifact store's own
    permissions test above is what made this omission easy to miss: the property
    was asserted for one store and merely implemented for the other.
    """
    from docdoc.artifacts import BlobStore

    blobs = BlobStore(tmp_path)
    blob_id = blobs.put(b"%PDF-1.4\nsomething confidential\n")

    path = next((tmp_path / "blobs").glob("*/*"))
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0, "the blob is readable by others"
    assert stat.S_IMODE(path.parent.stat().st_mode) & 0o077 == 0, "its fan-out directory is"
    assert stat.S_IMODE((tmp_path / "blobs").stat().st_mode) & 0o077 == 0, "the blob root is"
    assert blobs.get(blob_id) is not None, "and it is still readable by its owner"


def test_a_malformed_blob_id_is_refused_rather_than_resolved(tmp_path: Path) -> None:
    """An identity is a filename here, so a separator would escape the root."""
    from docdoc.artifacts import ArtifactError, BlobStore

    blobs = BlobStore(tmp_path)
    for bad in ("../../etc/passwd", "sha256:not-hex", "", "sha256:"):
        with pytest.raises(ArtifactError):
            blobs.get(bad)


def test_an_absent_blob_is_a_miss_and_not_an_error(tmp_path: Path) -> None:
    from docdoc.artifacts import BlobStore

    blobs = BlobStore(tmp_path)
    absent = "sha256:" + "0" * 64

    assert blobs.get(absent) is None
    assert blobs.size_of(absent) is None
