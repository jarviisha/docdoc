"""T052, T053 — the sweep's two operational promises.

**Bounded** (FR-015). A deployment that ran for a year before retention landed has
a year of rows, and one unbounded delete over them holds a transaction open for as
long as it takes. So a pass removes at most `batch`, and progress comes from many
passes — which is also what makes the worker's maintenance tick safe to give a
five-second budget.

**Degraded rather than destructive** (ADR-0015 §3). When the store cannot be
reached, the sweep removes **no run row at all** — not "the rows whose content it
managed to delete", and not "the rows, and the content next time". A run row holds
the `stage_outcomes` naming what still needs deleting, so a row removed while its
content survives takes the only record of that work with it, and no later sweep can
find those artifacts again.

That failure is silent, permanent, and looks like success. It is the reason this
file exists.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.artifacts.blobs import BlobStore
from docdoc.artifacts.errors import ArtifactError
from docdoc.artifacts.store import FileArtifactStore
from docdoc.runs import retention
from docdoc.runs.model import Run, RunStatus, StageOutcomeRecord

if TYPE_CHECKING:
    import pathlib

    import pytest

NOW = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
LONG_AGO = NOW - timedelta(days=90)


def _seed(queue: InMemoryRunQueue, count: int, *, tenant_id: str = "acme") -> list[Run]:
    runs = []
    for index in range(count):
        run = Run(
            run_id=uuid4(),
            tenant_id=tenant_id,
            blob_id="sha256:" + f"{index:064x}",
            schema_identity="invoice@1",
            status=RunStatus.SUCCEEDED,
            processing_id="sha256:" + f"{index + 1000:064x}",
            stage_outcomes=(
                StageOutcomeRecord(
                    stage="extract",
                    status="executed",
                    artifact_id="sha256:" + f"{index + 1000:064x}",
                ),
            ),
            created_at=LONG_AGO,
            updated_at=LONG_AGO,
            expires_at=LONG_AGO + timedelta(seconds=index),
        )
        queue._runs[run.run_id] = run
        runs.append(run)
    return runs


def _stores(root: pathlib.Path) -> retention.Stores:
    return retention.Stores(
        artifacts=FileArtifactStore(root, tenant_id="acme"),
        blobs=BlobStore(root, tenant_id="acme"),
    )


class TestTheBatchIsABound:
    def test_one_pass_removes_at_most_the_batch(self, tmp_path: pathlib.Path) -> None:
        queue = InMemoryRunQueue()
        _seed(queue, 25)

        report = retention.sweep(queue, lambda _t: _stores(tmp_path), now=NOW, batch=10)

        assert report.runs == 10
        assert len(queue._runs) == 15

    def test_a_backlog_larger_than_one_batch_makes_progress(self, tmp_path: pathlib.Path) -> None:
        """Many passes, which is what the worker's tick performs."""
        queue = InMemoryRunQueue()
        _seed(queue, 25)

        passes = 0
        while retention.sweep(queue, lambda _t: _stores(tmp_path), now=NOW, batch=10).runs:
            passes += 1
            assert passes < 10, "the sweep is not converging"

        assert queue._runs == {}
        assert passes == 3

    def test_the_oldest_deadline_goes_first(self, tmp_path: pathlib.Path) -> None:
        """So a bounded sweep is fair rather than arbitrary about what waits."""
        queue = InMemoryRunQueue()
        runs = _seed(queue, 5)

        retention.sweep(queue, lambda _t: _stores(tmp_path), now=NOW, batch=2)

        assert runs[0].run_id not in queue._runs
        assert runs[1].run_id not in queue._runs
        assert runs[4].run_id in queue._runs


class TestAnUnreachableStoreRemovesNothing:
    class _Unreachable:
        """Every delete raises, the way a store behind a dead mount does."""

        def delete(self, identity: str) -> bool:
            raise ArtifactError(
                "the store could not be reached", reason="unavailable", artifact_id=identity
            )

        def delete_prefix(self, *, allow_store_root: bool = False) -> int:
            raise ArtifactError("the store could not be reached", reason="unavailable")

    def test_no_run_row_is_removed(self, tmp_path: pathlib.Path) -> None:
        queue = InMemoryRunQueue()
        _seed(queue, 5)
        broken = retention.Stores(artifacts=self._Unreachable(), blobs=self._Unreachable())

        report = retention.sweep(queue, lambda _t: broken, now=NOW, batch=10)

        assert report.degraded is True
        assert report.runs == 0
        assert len(queue._runs) == 5, (
            "run rows were removed while their content survived. The row holds the "
            "stage_outcomes naming what still needs deleting, so this loses the "
            "only record of the work -- permanently, and silently (ADR-0015 s3)."
        )

    def test_no_tombstone_is_written_either(self, tmp_path: pathlib.Path) -> None:
        """A tombstone for a run that is still there would be a second lie."""
        queue = InMemoryRunQueue()
        runs = _seed(queue, 2)
        broken = retention.Stores(artifacts=self._Unreachable(), blobs=self._Unreachable())

        retention.sweep(queue, lambda _t: broken, now=NOW, batch=10)

        assert queue.tombstone(runs[0].run_id, "acme") is None

    def test_the_degradation_is_logged_once(
        self, tmp_path: pathlib.Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Once per sweep, not once per artifact it failed to delete."""
        queue = InMemoryRunQueue()
        _seed(queue, 10)
        broken = retention.Stores(artifacts=self._Unreachable(), blobs=self._Unreachable())

        with caplog.at_level(logging.WARNING, logger="docdoc.runs"):
            retention.sweep(queue, lambda _t: broken, now=NOW, batch=10)

        degraded = [
            record
            for record in caplog.records
            if getattr(record, "docdoc", {}).get("event") == "retention.degraded"
        ]
        assert len(degraded) == 1

    def test_the_next_sweep_succeeds_once_the_store_returns(self, tmp_path: pathlib.Path) -> None:
        """Nothing needs repairing: the batch is recomputed from what remains."""
        queue = InMemoryRunQueue()
        _seed(queue, 3)
        broken = retention.Stores(artifacts=self._Unreachable(), blobs=self._Unreachable())

        retention.sweep(queue, lambda _t: broken, now=NOW, batch=10)
        recovered = retention.sweep(queue, lambda _t: _stores(tmp_path), now=NOW, batch=10)

        assert recovered.runs == 3
        assert queue._runs == {}
