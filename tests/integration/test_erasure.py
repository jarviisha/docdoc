"""T062 to T066 - erasing a customer without erasing anyone else.

The test that matters most here is `test_the_default_tenant_is_refused`. ADR-0014's
Consequences section predicted, in Milestone 9, that this milestone would build the
naive version:

    "The default tenant is the one whose deletion is dangerous. Its prefix is the
    store root, so a naive 'delete tenant' implementation in Milestone 10 would
    delete everything. That is the correct semantics ... and it is written down
    here because it is the kind of correctness that reads as a bug at review time."

This file is that prediction, as a test. The semantics *are* correct — the default
tenant does own the root — which is exactly why they cannot be the default
behaviour: an operator erasing "the default tenant" in a deployment that never
enabled authentication is not erasing a customer.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.artifacts.blobs import BlobStore
from docdoc.artifacts.store import FileArtifactStore
from docdoc.runs import retention
from docdoc.runs.errors import RetentionError
from docdoc.runs.model import Run, RunStatus, StageOutcomeRecord

if TYPE_CHECKING:
    import pathlib

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)


def _stores(root: pathlib.Path, tenant_id: str) -> retention.Stores:
    return retention.Stores(
        artifacts=FileArtifactStore(root, tenant_id=tenant_id),
        blobs=BlobStore(root, tenant_id=tenant_id),
    )


def _seed_artifact(root: pathlib.Path, tenant_id: str, artifact_id: str) -> None:
    digest = artifact_id.split(":", 1)[-1]
    segment = "" if tenant_id == "default" else f"t/{tenant_id}/"
    path = root / f"{segment}artifacts" / digest[:2] / f"{digest}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")


def _exists(root: pathlib.Path, tenant_id: str, artifact_id: str) -> bool:
    digest = artifact_id.split(":", 1)[-1]
    segment = "" if tenant_id == "default" else f"t/{tenant_id}/"
    return (root / f"{segment}artifacts" / digest[:2] / f"{digest}.json").exists()


def _run(
    queue: InMemoryRunQueue,
    *,
    tenant_id: str,
    blob_id: str,
    artifact_id: str,
    status: RunStatus = RunStatus.SUCCEEDED,
) -> Run:
    run = Run(
        run_id=uuid4(),
        tenant_id=tenant_id,
        blob_id=blob_id,
        schema_identity="invoice@1",
        status=status,
        processing_id=artifact_id if status is RunStatus.SUCCEEDED else None,
        stage_outcomes=(
            StageOutcomeRecord(stage="extract", status="executed", artifact_id=artifact_id),
        ),
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(days=1),
        expires_at=NOW + timedelta(days=30),
    )
    queue._runs[run.run_id] = run
    return run


class TestOneTenantAndNotTheOthers:
    def test_byte_identical_documents_are_erased_separately(self, tmp_path: pathlib.Path) -> None:
        """FR-008, SC-004. The case content-addressing makes ordinary."""
        queue = InMemoryRunQueue()
        shared_artifact = "sha256:" + "7f" * 32
        for tenant in ("acme", "globex"):
            _seed_artifact(tmp_path, tenant, shared_artifact)
            blob_id = BlobStore(tmp_path, tenant_id=tenant).put(b"the same invoice")
            _run(queue, tenant_id=tenant, blob_id=blob_id, artifact_id=shared_artifact)

        report = retention.erase(queue, _stores(tmp_path, "acme"), tenant_id="acme", now=NOW)

        assert report.runs == 1
        assert not _exists(tmp_path, "acme", shared_artifact)
        assert _exists(tmp_path, "globex", shared_artifact), (
            "erasing one tenant reached another's content. Two tenants submitting "
            "the same bytes arrive at the same identities independently, and the "
            "namespaces are the only thing keeping them apart (ADR-0014)."
        )
        assert BlobStore(tmp_path, tenant_id="globex").get(
            BlobStore(tmp_path, tenant_id="globex").put(b"the same invoice")
        ), "the other tenant's blob went too"

    def test_the_survivors_runs_still_resolve(self, tmp_path: pathlib.Path) -> None:
        queue = InMemoryRunQueue()
        for tenant in ("acme", "globex"):
            blob_id = BlobStore(tmp_path, tenant_id=tenant).put(b"a document")
            _run(
                queue,
                tenant_id=tenant,
                blob_id=blob_id,
                artifact_id="sha256:" + "11" * 32,
            )
        survivor = next(run for run in queue._runs.values() if run.tenant_id == "globex")

        retention.erase(queue, _stores(tmp_path, "acme"), tenant_id="acme", now=NOW)

        assert queue.get(survivor.run_id, "globex") is not None


class TestTheDefaultTenantIsRefused:
    """ADR-0014's predicted bug, as a test."""

    def test_a_caller_who_mislabels_the_tenant_is_still_refused(
        self, tmp_path: pathlib.Path
    ) -> None:
        """The safety net under the decision, and it is worth having.

        `erase` decides which path to take from `is_default_tenant`, which a
        caller supplies. If a caller gets that wrong — a new route, a script, a
        refactor that loses the flag — the *store* still refuses, because its
        guard tests the danger directly (`_base == root`) rather than trusting a
        label it was handed.

        Two independent checks for one destructive operation, which is the right
        number for an operation that cannot be undone.
        """
        queue = InMemoryRunQueue()
        BlobStore(tmp_path).put(b"written before authentication existed")

        with pytest.raises(RetentionError):
            retention.erase(
                queue,
                _stores(tmp_path, "default"),
                tenant_id="default",
                now=NOW,
                is_default_tenant=False,  # the caller's mistake
            )

    def test_without_the_flag_it_takes_the_difference(self, tmp_path: pathlib.Path) -> None:
        """The default and the safe one: run-derived content, and nothing else."""
        queue = InMemoryRunQueue()
        cli_written = "sha256:" + "c1" * 32
        _seed_artifact(tmp_path, "default", cli_written)

        blobs = BlobStore(tmp_path)
        blob_id = blobs.put(b"submitted through a run")
        run_artifact = "sha256:" + "d1" * 32
        _seed_artifact(tmp_path, "default", run_artifact)
        _run(queue, tenant_id="default", blob_id=blob_id, artifact_id=run_artifact)

        report = retention.erase(
            queue,
            _stores(tmp_path, "default"),
            tenant_id="default",
            now=NOW,
            is_default_tenant=True,
        )

        assert report.runs == 1
        assert not _exists(tmp_path, "default", run_artifact)
        assert _exists(tmp_path, "default", cli_written), (
            "erasing the default tenant took content no run ever produced. Its "
            "namespace is the store root, so the prefix path is the whole store "
            "(ADR-0014 section 3, ADR-0015 section 5)."
        )

    def test_the_flag_takes_the_root(self, tmp_path: pathlib.Path) -> None:
        """The other reading, available and typed deliberately."""
        queue = InMemoryRunQueue()
        _seed_artifact(tmp_path, "default", "sha256:" + "c1" * 32)
        BlobStore(tmp_path).put(b"anything at all")

        report = retention.erase(
            queue,
            _stores(tmp_path, "default"),
            tenant_id="default",
            now=NOW,
            is_default_tenant=True,
            allow_store_root=True,
        )

        assert report.artifacts == 1
        assert report.blobs == 1


class TestErasureIsIdempotent:
    def test_twice(self, tmp_path: pathlib.Path) -> None:
        queue = InMemoryRunQueue()
        blob_id = BlobStore(tmp_path, tenant_id="acme").put(b"gone")
        _run(queue, tenant_id="acme", blob_id=blob_id, artifact_id="sha256:" + "22" * 32)

        first = retention.erase(queue, _stores(tmp_path, "acme"), tenant_id="acme", now=NOW)
        second = retention.erase(queue, _stores(tmp_path, "acme"), tenant_id="acme", now=NOW)

        assert first.runs == 1
        assert second.runs == 0

    def test_a_tenant_that_never_existed(self, tmp_path: pathlib.Path) -> None:
        """FR-009: succeeds having removed nothing, rather than failing."""
        report = retention.erase(
            InMemoryRunQueue(), _stores(tmp_path, "nobody"), tenant_id="nobody", now=NOW
        )

        assert (report.runs, report.artifacts, report.blobs) == (0, 0, 0)


class TestInFlightRunsAreCancelledFirst:
    def test_a_queued_run_is_cancelled_before_its_namespace_goes(
        self, tmp_path: pathlib.Path
    ) -> None:
        """FR-010, and the order is the point.

        Erasure must never leave a worker executing into a namespace that has
        just been removed — it would write artifacts nobody can reach, having
        paid a provider for them.
        """
        queue = InMemoryRunQueue()
        blob_id = BlobStore(tmp_path, tenant_id="acme").put(b"still queued")
        queued = _run(
            queue,
            tenant_id="acme",
            blob_id=blob_id,
            artifact_id="sha256:" + "33" * 32,
            status=RunStatus.QUEUED,
        )
        assert queued.status is RunStatus.QUEUED

        retention.erase(queue, _stores(tmp_path, "acme"), tenant_id="acme", now=NOW)

        assert queue.get(queued.run_id, "acme") is None
        assert queue.tombstone(queued.run_id, "acme") is not None


class TestErasingOneDocument:
    def test_only_the_runs_over_that_blob(self, tmp_path: pathlib.Path) -> None:
        """FR-005. A prefix cannot express "one blob", so this is the difference."""
        queue = InMemoryRunQueue()
        blobs = BlobStore(tmp_path, tenant_id="acme")
        doomed_blob = blobs.put(b"the document being erased")
        kept_blob = blobs.put(b"a different document")
        doomed_artifact, kept_artifact = "sha256:" + "44" * 32, "sha256:" + "55" * 32
        _seed_artifact(tmp_path, "acme", doomed_artifact)
        _seed_artifact(tmp_path, "acme", kept_artifact)
        _run(queue, tenant_id="acme", blob_id=doomed_blob, artifact_id=doomed_artifact)
        kept = _run(queue, tenant_id="acme", blob_id=kept_blob, artifact_id=kept_artifact)

        report = retention.erase(
            queue, _stores(tmp_path, "acme"), tenant_id="acme", now=NOW, blob_id=doomed_blob
        )

        assert report.runs == 1
        assert not _exists(tmp_path, "acme", doomed_artifact)
        assert _exists(tmp_path, "acme", kept_artifact)
        assert queue.get(kept.run_id, "acme") is not None
        assert blobs.get(kept_blob) is not None

    def test_the_policy_names_the_document(self, tmp_path: pathlib.Path) -> None:
        queue = InMemoryRunQueue()
        blob_id = BlobStore(tmp_path, tenant_id="acme").put(b"one document")
        run = _run(queue, tenant_id="acme", blob_id=blob_id, artifact_id="sha256:" + "66" * 32)

        retention.erase(
            queue, _stores(tmp_path, "acme"), tenant_id="acme", now=NOW, blob_id=blob_id
        )

        assert queue.tombstone(run.run_id, "acme").policy == "erasure:document"  # type: ignore[union-attr]


class TestNoRowSurvivesAnErasure:
    """T065a, FR-086 — the tables an erasure is easiest to forget."""

    def _seed_side_rows(self, queue: InMemoryRunQueue, tenant_id: str) -> None:
        for table in queue._side_tables:
            queue._side_tables[table][f"{tenant_id}-1"] = tenant_id

    def test_every_table_this_milestone_adds_is_emptied(self, tmp_path: pathlib.Path) -> None:
        queue = InMemoryRunQueue()
        blob_id = BlobStore(tmp_path, tenant_id="acme").put(b"a document")
        _run(queue, tenant_id="acme", blob_id=blob_id, artifact_id="sha256:" + "77" * 32)
        self._seed_side_rows(queue, "acme")

        report = retention.erase(queue, _stores(tmp_path, "acme"), tenant_id="acme", now=NOW)

        assert report.rows == 4, (
            "an erasure left rows behind. `corrections` is the one that matters: "
            "it holds the predicted and corrected VALUES a reviewer stated, so it "
            "is the only table this milestone adds that can carry content taken "
            "from a document (FR-086)."
        )
        assert all(not rows for rows in queue._side_tables.values())

    def test_another_tenants_rows_are_untouched(self, tmp_path: pathlib.Path) -> None:
        queue = InMemoryRunQueue()
        self._seed_side_rows(queue, "acme")
        self._seed_side_rows(queue, "globex")

        retention.erase(queue, _stores(tmp_path, "acme"), tenant_id="acme", now=NOW)

        assert all(rows == {"globex-1": "globex"} for rows in queue._side_tables.values())

    def test_the_difference_path_purges_too(self, tmp_path: pathlib.Path) -> None:
        """The gap a code review found, as a test.

        `purge_tenant_rows` was called only from the prefix branch, so erasing
        the **default** tenant -- the one case ADR-0015 §5 forces onto the set
        difference -- left its corrections, callbacks, deliveries, and counters
        behind. The earlier test passed because it used `acme`, which takes the
        prefix path.
        """
        queue = InMemoryRunQueue()
        blob_id = BlobStore(tmp_path).put(b"a document")
        _run(queue, tenant_id="default", blob_id=blob_id, artifact_id="sha256:" + "99" * 32)
        self._seed_side_rows(queue, "default")

        report = retention.erase(
            queue,
            _stores(tmp_path, "default"),
            tenant_id="default",
            now=NOW,
            is_default_tenant=True,
        )

        assert report.rows == 4
        assert all(not rows for rows in queue._side_tables.values())

    def test_a_sweep_purges_nothing(self, tmp_path: pathlib.Path) -> None:
        """The other direction, and the one that would be a data-loss bug.

        A sweep removes a run that aged out. A tenant's callbacks and counters
        are not that run's, and dropping them would make retention quietly
        deconfigure a customer.
        """
        queue = InMemoryRunQueue()
        blob_id = BlobStore(tmp_path, tenant_id="acme").put(b"expired")
        run = _run(queue, tenant_id="acme", blob_id=blob_id, artifact_id="sha256:" + "aa" * 32)
        queue._runs[run.run_id] = run.model_copy(update={"expires_at": NOW - timedelta(days=1)})
        self._seed_side_rows(queue, "acme")

        report = retention.sweep(queue, lambda _t: _stores(tmp_path, "acme"), now=NOW, batch=10)

        assert report.runs == 1
        assert report.rows == 0
        assert all(rows for rows in queue._side_tables.values()), (
            "a retention sweep removed a tenant's callbacks or counters. A sweep "
            "removes one run; deconfiguring the customer is not part of that."
        )

    def test_erasing_one_document_keeps_the_tenants_configuration(
        self, tmp_path: pathlib.Path
    ) -> None:
        """FR-086 scoped: the tenant is not being erased, so its setup stays."""
        queue = InMemoryRunQueue()
        blobs = BlobStore(tmp_path, tenant_id="acme")
        blob_id = blobs.put(b"the document being erased")
        run = _run(queue, tenant_id="acme", blob_id=blob_id, artifact_id="sha256:" + "bb" * 32)
        queue._side_tables["callbacks"]["acme-1"] = "acme"
        queue._side_tables["limit_counters"]["acme-1"] = "acme"
        queue._side_tables["corrections"][f"c@{run.run_id}"] = "acme"

        retention.erase(
            queue, _stores(tmp_path, "acme"), tenant_id="acme", now=NOW, blob_id=blob_id
        )

        assert queue._side_tables["corrections"] == {}
        assert queue._side_tables["callbacks"] == {"acme-1": "acme"}
        assert queue._side_tables["limit_counters"] == {"acme-1": "acme"}

    def test_the_tombstone_is_kept(self, tmp_path: pathlib.Path) -> None:
        """It is what lets the owner be told their run was here (FR-011).

        Purging it along with everything else would make an erased run and a run
        that never existed indistinguishable *to its owner*, which is the one
        party entitled to know the difference.
        """
        queue = InMemoryRunQueue()
        blob_id = BlobStore(tmp_path, tenant_id="acme").put(b"a document")
        run = _run(queue, tenant_id="acme", blob_id=blob_id, artifact_id="sha256:" + "88" * 32)

        retention.erase(queue, _stores(tmp_path, "acme"), tenant_id="acme", now=NOW)

        assert queue.tombstone(run.run_id, "acme") is not None
