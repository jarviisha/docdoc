"""FR-005 — the same document twice is two attempts and one result.

This is ADR-0013 §1's argument for two identities, as a test. A `run_id` is an
*attempt* and a `processing_id` is a *result*, so submitting one document twice
must produce two of the first and one of the second. Collapsing them — returning
the original run for a repeat submission — would be the obvious "helpful"
behaviour and it would make the second submission unobservable: the caller asked
for something and got a receipt for something else.

**Counted, not timed.** The second run must invoke the parser zero times. A
stopwatch would be flaky on a slow machine and, far worse, would *pass* on a fast
one that re-parsed everything, which is the failure this assertion exists for.

Offline: the in-memory queue and `execute_one`. The question is about the run
model and the artifact chain, and neither becomes truer with a database.
`test_async_matches_sync.py` asks the same question against Postgres, where what
it additionally proves is that the row survives.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("pymupdf", reason="a real parse is what is being counted")

from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.runs.identity import DEFAULT_LEASE, new_run_id
from docdoc.runs.model import DEFAULT_TENANT, RunStatus
from docdoc.runs.worker import execute_one

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"
NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)


@dataclass
class Spec:
    blob_id: str
    tenant_id: str = DEFAULT_TENANT
    schema_identity: str = SCHEMA
    request_id: str | None = None
    idempotency_key: str | None = None


class CountingAdapter:
    """The echo adapter with a counter on `complete`, the billable call.

    Blanket delegation, and the reason is recorded at length in
    `test_shared_store_reuse.py`: an adapter's *cache identity* is wider than the
    `ModelAdapter` protocol, because `pipeline/plan.py` folds `model_id` and
    `model_version` into the extract stage's options hash. A wrapper forwarding
    only the protocol produces a different artifact id, so the second run misses
    and this test reports a reuse failure that exists only in its own double.
    """

    def __init__(self) -> None:
        self._inner = EchoAdapter.from_fixtures("tests/fixtures/echo")
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self._inner.complete(*args, **kwargs)


def _run_once(queue: InMemoryRunQueue, blobs: BlobStore, store: Any, blob_id: str, adapter: Any):
    submitted = queue.submit(
        Spec(blob_id=blob_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    claimed = queue.claim(worker_id="w", now=NOW, lease=DEFAULT_LEASE, max_attempts=3)
    assert claimed is not None
    result = execute_one(
        claimed,
        queue=queue,
        blobs=blobs,
        store=store,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=adapter,
        now=NOW,
    )
    finished = queue.get(submitted.run_id, DEFAULT_TENANT)
    assert finished is not None
    assert finished.status is RunStatus.SUCCEEDED, (
        f"the run failed at {finished.failed_stage} with {finished.error_class}"
    )
    return finished, result


def test_two_submissions_are_two_runs_and_one_result(tmp_path: Path) -> None:
    """The whole of FR-005 in three assertions."""
    queue = InMemoryRunQueue()
    blobs = BlobStore(tmp_path)
    store = FileArtifactStore(tmp_path)
    blob_id = blobs.put(FIXTURE.read_bytes())
    adapter = CountingAdapter()

    first, _ = _run_once(queue, blobs, store, blob_id, adapter)
    after_first = adapter.calls
    second, result = _run_once(queue, blobs, store, blob_id, adapter)

    assert first.run_id != second.run_id, "two submissions collapsed into one attempt"
    assert first.processing_id == second.processing_id, (
        "two runs over one document produced two results; the terminal identity "
        "is derived from the inputs, so identical inputs must derive identically"
    )
    assert after_first > 0, "the counter never moved; this test measures nothing"
    assert adapter.calls == after_first, (
        f"the second run made {adapter.calls - after_first} model calls. Every "
        "artifact was already in the store, so reuse did not happen and the "
        "second run paid again for work already done"
    )
    assert result is not None
    assert result.executed_count == 0, (
        f"{result.executed_count} stages executed on the second run; a document "
        "already processed should cost nothing to process again"
    )


def test_an_idempotency_key_makes_the_second_submission_the_first_run(
    tmp_path: Path,
) -> None:
    """FR-011, which is the *other* answer and needs asking for explicitly.

    Two runs is the default because two submissions are two intentions. A caller
    who means "this might be a retry" says so with a header, and then — and only
    then — gets the original run back.
    """
    queue = InMemoryRunQueue()
    blobs = BlobStore(tmp_path)
    blob_id = blobs.put(FIXTURE.read_bytes())

    def submit(key: str | None):
        spec = Spec(blob_id=blob_id)
        spec.idempotency_key = key
        return queue.submit(
            spec,  # type: ignore[arg-type]
            run_id=new_run_id(),
            now=NOW,
            expires_at=NOW + timedelta(days=30),
        )

    keyed = submit("retry-1")
    again = submit("retry-1")
    unkeyed_a = submit(None)
    unkeyed_b = submit(None)

    assert keyed.run_id == again.run_id
    assert unkeyed_a.run_id != unkeyed_b.run_id
