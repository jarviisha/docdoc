"""Two processes, one artifact, no lock.

The claim in ADR-0010 §5 is that atomic replacement of an immutable,
content-addressed entry makes the race benign — so the store needs no lock, no
lease, and no coordinator. That is a pleasant claim and an easy one to be wrong
about, so it is exercised rather than asserted.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pytest
from pydantic import BaseModel

from docdoc.artifacts import ArtifactError, FileArtifactStore

ID = "sha256:" + "a" * 64


class _Result(BaseModel):
    value: str


def _put(store: FileArtifactStore, value: str) -> None:
    store.put(
        ID,
        _Result(value=value),
        stage="extract",
        input_artifact_id=None,
        processor_id="extractor",
        processor_version="1.0.0",
        options_hash="sha256:" + "d" * 64,
        artifact_format_version=1,
    )


def _race(store: FileArtifactStore, values: list[str], workers: int) -> list[BaseException]:
    """Run `workers` writers as simultaneously as threads allow."""
    barrier = threading.Barrier(workers)
    failures: list[BaseException] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        barrier.wait()
        try:
            _put(store, values[index % len(values)])
        except BaseException as error:
            with lock:
                failures.append(error)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    return failures


def test_concurrent_identical_writes_all_succeed(tmp_path: Path) -> None:
    """The benign race: same inputs, same result, same bytes.

    Whichever writer wins, the file is the same file. Nothing is lost and nothing
    needed to be serialised.
    """
    store = FileArtifactStore(tmp_path / "store")
    failures = _race(store, ["INV-001"], workers=8)

    assert failures == []
    assert store.get(ID, model=_Result, artifact_format_version=1) == _Result(value="INV-001")


def test_the_stored_file_is_never_half_written_under_a_race(tmp_path: Path) -> None:
    store = FileArtifactStore(tmp_path / "store")
    _race(store, ["INV-001"], workers=8)

    leftovers = list((store.root / "artifacts").glob("**/*.tmp"))
    assert leftovers == []
    # Readable, complete, and verifying its own content id.
    assert store.envelope(ID) is not None


def test_concurrent_divergent_writes_do_not_silently_pick_a_winner(tmp_path: Path) -> None:
    """The malign race, and the reason last-write-wins was rejected.

    Two disagreeing results under one identity mean a processor changed its
    output without changing its version. At least one writer must be told;
    quietly keeping one of the two is how that goes unnoticed for a release.
    """
    store = FileArtifactStore(tmp_path / "store")
    failures = _race(store, ["INV-001", "INV-999"], workers=8)

    stored = store.get(ID, model=_Result, artifact_format_version=1)
    assert stored is not None
    assert stored.value in {"INV-001", "INV-999"}

    # Everyone who lost the race and carried different bytes was told so.
    assert failures, "a divergent concurrent write was accepted in silence"
    assert all(isinstance(f, ArtifactError) and f.reason == "conflict" for f in failures)


def test_a_reader_racing_a_writer_never_sees_a_partial_artifact(tmp_path: Path) -> None:
    """A reader sees the previous state or the complete new one."""
    store = FileArtifactStore(tmp_path / "store")
    seen: list[object] = []
    stop = threading.Event()

    def reader() -> None:
        while not stop.is_set():
            try:
                seen.append(store.get(ID, model=_Result, artifact_format_version=1))
            except ArtifactError as error:  # pragma: no cover - the failure this forbids
                pytest.fail(f"a reader saw a partial artifact: {error}")

    thread = threading.Thread(target=reader)
    thread.start()
    try:
        for _ in range(50):
            _put(store, "INV-001")
    finally:
        stop.set()
        thread.join()

    assert all(value in (None, _Result(value="INV-001")) for value in seen)
