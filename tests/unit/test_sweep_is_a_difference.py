"""T047 — the most important test in this milestone.

The sweep's deletion set is a **difference**:

    candidates = the artifacts of the runs being removed
    survivors  = the artifacts of that tenant's remaining runs
    delete       candidates - survivors

The rejected form is one word away in English and a catastrophe away in
behaviour: *"delete every artifact no surviving run references"* is a
**complement**, and it removes everything `docdoc extract` ever wrote from the
command line, everything a library caller produced, and every artifact the
recorder wrote — because none of those has a run row, so all of them are
unreferenced by construction.

That version passes a test suite whose fixtures only ever create runs. This file
creates content the way the command line does — with no run at all — and then
sweeps.

**If this fails, do not adjust the assertion.** The sweep has become a complement
and a deployment running it will lose data that no retention policy ever selected.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from docdoc.artifacts.blobs import BlobStore
from docdoc.artifacts.store import FileArtifactStore
from docdoc.runs import retention
from docdoc.runs.model import Run, RunStatus, StageOutcomeRecord
from tests.fixtures.run_queue import InMemoryRunQueue

if TYPE_CHECKING:
    import pathlib

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=90)


def _run(
    *,
    tenant_id: str = "acme",
    blob_id: str,
    artifact_ids: tuple[str, ...],
    expires_at: datetime,
) -> Run:
    return Run(
        run_id=uuid4(),
        tenant_id=tenant_id,
        blob_id=blob_id,
        schema_identity="invoice@1",
        status=RunStatus.SUCCEEDED,
        # A succeeded run carries one, and the CHECK constraint in 0001 says so.
        processing_id=artifact_ids[-1] if artifact_ids else "sha256:" + "00" * 32,
        stage_outcomes=tuple(
            StageOutcomeRecord(stage=f"stage-{index}", status="executed", artifact_id=identity)
            for index, identity in enumerate(artifact_ids)
        ),
        created_at=LONG_AGO,
        updated_at=LONG_AGO,
        expires_at=expires_at,
    )


def _stores(root: pathlib.Path, tenant_id: str = "acme") -> retention.Stores:
    return retention.Stores(
        artifacts=FileArtifactStore(root, tenant_id=tenant_id),
        blobs=BlobStore(root, tenant_id=tenant_id),
    )


def _seed_artifact(root: pathlib.Path, tenant_id: str, artifact_id: str) -> None:
    """Write an artifact file directly, the way the store lays them out.

    Direct rather than through `put`, because `put` needs a payload model and an
    envelope and this test cares only about a file being present at the identity
    the sweep will reach for.
    """
    digest = artifact_id.split(":", 1)[-1]
    segment = "" if tenant_id == "default" else f"t/{tenant_id}/"
    path = root / f"{segment}artifacts" / digest[:2] / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


def _exists(root: pathlib.Path, tenant_id: str, artifact_id: str) -> bool:
    digest = artifact_id.split(":", 1)[-1]
    segment = "" if tenant_id == "default" else f"t/{tenant_id}/"
    return (root / f"{segment}artifacts" / digest[:2] / f"{digest}.json").exists()


def test_content_with_no_run_row_survives_the_sweep(tmp_path: pathlib.Path) -> None:
    """The CLI hazard, exactly as an operator would meet it.

    `docdoc extract` writes artifacts and creates no run. A complement deletes
    them on the first sweep the deployment ever performs; a difference cannot
    reach them, because an artifact becomes a candidate only by being named by a
    run that is being removed.
    """
    queue = InMemoryRunQueue()
    cli_written = "sha256:" + "c1" * 32
    _seed_artifact(tmp_path, "acme", cli_written)

    blobs = BlobStore(tmp_path, tenant_id="acme")
    doomed_blob = blobs.put(b"a document only the expired run used")
    doomed_artifact = "sha256:" + "d1" * 32
    _seed_artifact(tmp_path, "acme", doomed_artifact)
    expired = _run(blob_id=doomed_blob, artifact_ids=(doomed_artifact,), expires_at=LONG_AGO)
    # Seeding state the fixture has no public verb for: a run that is already
    # terminal and already expired.
    queue._runs[expired.run_id] = expired

    report = retention.sweep(queue, lambda _tenant: _stores(tmp_path), now=NOW, batch=100)

    assert report.runs == 1
    assert not _exists(tmp_path, "acme", doomed_artifact), "the expired run's artifact should go"
    assert _exists(tmp_path, "acme", cli_written), (
        "the sweep deleted an artifact no run ever named. It has become a "
        "COMPLEMENT: 'everything no surviving run references' reaches every "
        "artifact the command line, the library, and the recorder ever wrote. "
        "The deletion set is a DIFFERENCE (ADR-0015 section 1)."
    )


def test_an_artifact_two_runs_share_survives_while_one_of_them_goes(
    tmp_path: pathlib.Path,
) -> None:
    """Reuse is the point of content-addressing, so it is the point here too."""
    queue = InMemoryRunQueue()
    blobs = BlobStore(tmp_path, tenant_id="acme")
    blob_id = blobs.put(b"one document, submitted twice")
    shared = "sha256:" + "5a" * 32
    _seed_artifact(tmp_path, "acme", shared)

    expired = _run(blob_id=blob_id, artifact_ids=(shared,), expires_at=LONG_AGO)
    surviving = _run(blob_id=blob_id, artifact_ids=(shared,), expires_at=NOW + timedelta(days=30))
    queue._runs[expired.run_id] = expired
    queue._runs[surviving.run_id] = surviving

    report = retention.sweep(queue, lambda _tenant: _stores(tmp_path), now=NOW, batch=100)

    assert report.runs == 1
    assert report.artifacts == 0
    assert _exists(tmp_path, "acme", shared)
    assert blobs.get(blob_id) is not None, (
        "the blob went while a surviving run still names it. A blob survives on "
        "exactly the terms an artifact does -- two runs over the same document "
        "share one, which is the most ordinary thing a tenant does."
    )


def test_another_tenants_content_is_never_a_candidate(tmp_path: pathlib.Path) -> None:
    """Byte-identical documents across tenants, which is FR-008's case."""
    queue = InMemoryRunQueue()
    shared_identity = "sha256:" + "7f" * 32
    _seed_artifact(tmp_path, "acme", shared_identity)
    _seed_artifact(tmp_path, "globex", shared_identity)

    acme_blob = BlobStore(tmp_path, tenant_id="acme").put(b"same bytes")
    expired = _run(blob_id=acme_blob, artifact_ids=(shared_identity,), expires_at=LONG_AGO)
    queue._runs[expired.run_id] = expired

    retention.sweep(queue, lambda tenant: _stores(tmp_path, tenant), now=NOW, batch=100)

    assert not _exists(tmp_path, "acme", shared_identity)
    assert _exists(tmp_path, "globex", shared_identity), (
        "one tenant's sweep reached another's content. Identity is derived from "
        "content, so two tenants arrive at the same artifact id independently "
        "(ADR-0014); the namespaces are what keep them apart."
    )


def test_a_second_pass_removes_nothing(tmp_path: pathlib.Path) -> None:
    """FR-002, SC-003: idempotent, because a sweep dies halfway."""
    queue = InMemoryRunQueue()
    blob_id = BlobStore(tmp_path, tenant_id="acme").put(b"gone")
    artifact_id = "sha256:" + "e2" * 32
    _seed_artifact(tmp_path, "acme", artifact_id)
    expired = _run(blob_id=blob_id, artifact_ids=(artifact_id,), expires_at=LONG_AGO)
    queue._runs[expired.run_id] = expired

    first = retention.sweep(queue, lambda _t: _stores(tmp_path), now=NOW, batch=100)
    second = retention.sweep(queue, lambda _t: _stores(tmp_path), now=NOW, batch=100)

    assert (first.runs, first.artifacts, first.blobs) == (1, 1, 1)
    assert (second.runs, second.artifacts, second.blobs) == (0, 0, 0)


def test_a_live_run_is_never_a_candidate(tmp_path: pathlib.Path) -> None:
    """FR-003: a policy that can delete work in flight loses paid work."""
    queue = InMemoryRunQueue()
    blob_id = BlobStore(tmp_path, tenant_id="acme").put(b"in flight")
    running = _run(blob_id=blob_id, artifact_ids=(), expires_at=LONG_AGO).model_copy(
        update={"status": RunStatus.RUNNING}
    )
    queue._runs[running.run_id] = running

    assert retention.sweep(queue, lambda _t: _stores(tmp_path), now=NOW, batch=100).runs == 0


def test_a_pinned_run_survives(tmp_path: pathlib.Path) -> None:
    """FR-013 — the one place this milestone's two halves touch."""

    class Pinning:
        def pinned_runs(self, run_ids, *, at):  # type: ignore[no-untyped-def]
            return frozenset(run_ids)

    queue = InMemoryRunQueue()
    blob_id = BlobStore(tmp_path, tenant_id="acme").put(b"corrected")
    expired = _run(blob_id=blob_id, artifact_ids=(), expires_at=LONG_AGO)
    queue._runs[expired.run_id] = expired

    report = retention.sweep(
        queue, lambda _t: _stores(tmp_path), now=NOW, batch=100, corrections=Pinning()
    )

    assert (report.runs, report.pinned) == (0, 1)
