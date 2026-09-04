"""Two workers, one store, and the parser called once (SC-005).

This is the test for the failure that has **no symptom**. Give each worker a
private store root and everything still works: every run produces a correct
result, no error is logged, no metric moves. The only evidence is the bill.

So the assertion is on an invocation counter and never on elapsed time. A
stopwatch would make this test flaky and, worse, would pass on a fast machine
that was re-parsing every document.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.infra import require_database, require_s3_endpoint

from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.runs import migrations
from docdoc.runs.identity import DEFAULT_LEASE, new_run_id
from docdoc.runs.model import DEFAULT_TENANT, RunStatus
from docdoc.runs.postgres import PostgresRunQueue
from docdoc.runs.worker import execute_one

pytestmark = [pytest.mark.postgres, pytest.mark.s3]

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
BUCKET = "docdoc"


@dataclass
class Spec:
    blob_id: str
    tenant_id: str = DEFAULT_TENANT
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None


class CountingAdapter:
    """Wraps the echo adapter and counts what it was asked to do.

    A counter rather than a spy on the parser itself, because the extract stage
    is the one whose reuse this test is really about: it is the billable one, and
    it is the one a private store root would silently repeat.
    """

    def __init__(self) -> None:
        self._inner = EchoAdapter.from_fixtures("tests/fixtures/echo")
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        """Delegate everything not defined here.

        Blanket delegation rather than an enumerated list, because **an adapter's
        cache identity is wider than the `ModelAdapter` protocol**.
        `pipeline/plan.py` reads `model_id` and `model_version` — which the
        protocol does not declare — to build the extract stage's `options_hash`.
        A wrapper forwarding only `id` and `version` produces a *different*
        artifact id, so the second run misses the cache and re-pays, and this
        test reports a reuse failure that exists only in its own test double.

        That is what happened while writing this file, and enumerating the
        attributes would only move the problem to whichever one is added next.
        """
        return getattr(self._inner, name)

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        """The one method the extract stage calls, and the billable one.

        `complete`, not `extract`: that is the `ModelAdapter` protocol. An
        earlier version of this wrapper defined `extract`, so the pipeline went
        past it to the inner adapter, the counter stayed at zero, and the reuse
        assertion passed for the worst possible reason.
        """
        self.calls += 1
        return self._inner.complete(*args, **kwargs)


@pytest.fixture
def shared(request: pytest.FixtureRequest):
    """One Postgres queue and one S3-backed store pair, emptied first."""
    psycopg = pytest.importorskip("psycopg")
    pytest.importorskip("boto3")
    from docdoc.artifacts.s3 import S3ArtifactStore, S3BlobStore, s3_client

    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs")

    client = s3_client(
        endpoint_url=require_s3_endpoint(),
        aws_access_key_id="docdoc",
        aws_secret_access_key="docdocdocdoc",
    )
    prefix = f"test/{request.node.name}"
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for entry in page.get("Contents", ()):
            client.delete_object(Bucket=BUCKET, Key=entry["Key"])

    return (
        PostgresRunQueue(lambda: psycopg.connect(dsn)),
        S3BlobStore(BUCKET, client=client, prefix=prefix),
        S3ArtifactStore(BUCKET, client=client, prefix=prefix),
    )


def test_a_second_worker_reuses_the_first_workers_artifacts(shared) -> None:
    """SC-005. The parse the first worker paid for is the parse the second uses.

    The two workers are two `execute_one` calls with two adapter instances —
    which is what two processes are, minus the process. Nothing in the worker
    holds state between runs, so sharing nothing but the store and the queue is
    a faithful simulation of the real thing.
    """
    queue, blobs, store = shared
    blob_id = blobs.put(FIXTURE.read_bytes())
    registry = SchemaRegistry.from_paths([Path("schemas")])
    now = datetime.now(UTC)

    results = []
    adapters = []
    for worker in ("w1", "w2"):
        queue.submit(
            Spec(blob_id=blob_id),  # type: ignore[arg-type]
            run_id=new_run_id(),
            now=now,
            expires_at=now + timedelta(days=30),
        )
        claimed = queue.claim(worker_id=worker, now=now, lease=DEFAULT_LEASE, max_attempts=3)
        assert claimed is not None

        adapter = CountingAdapter()
        adapters.append(adapter)
        result = execute_one(
            claimed,
            queue=queue,
            blobs=blobs,
            store=store,
            registry=registry,
            adapter=adapter,
            now=now,
        )
        assert result is not None
        results.append(result)

        finished = queue.get(claimed.run_id, DEFAULT_TENANT)
        assert finished is not None
        assert finished.status is RunStatus.SUCCEEDED

    assert adapters[0].calls == 1, "the first worker must actually do the work"
    assert adapters[1].calls == 0, (
        "the second worker called the model adapter. Every artifact the first "
        "worker wrote was in the shared store, so this means reuse did not "
        "happen — the failure with no symptom: correct results, and a second "
        "invoice for work already done"
    )

    assert results[1].executed_count == 0, (
        f"the second run executed {results[1].executed_count} stages; all four were already stored"
    )
    assert results[0].processing_id == results[1].processing_id


def test_private_store_roots_are_what_this_test_would_catch(shared) -> None:
    """The counter-example, so the assertion above is known to have teeth.

    Two workers with *separate* stores must show the model adapter called twice.
    Without this, `calls == 0` above could be passing because the counter never
    increments at all.
    """
    queue, blobs, _shared_store = shared
    import tempfile

    from docdoc.artifacts import FileArtifactStore

    blob_id = blobs.put(FIXTURE.read_bytes())
    registry = SchemaRegistry.from_paths([Path("schemas")])
    now = datetime.now(UTC)

    calls = []
    for worker in ("w1", "w2"):
        queue.submit(
            Spec(blob_id=blob_id),  # type: ignore[arg-type]
            run_id=new_run_id(),
            now=now,
            expires_at=now + timedelta(days=30),
        )
        claimed = queue.claim(worker_id=worker, now=now, lease=DEFAULT_LEASE, max_attempts=3)
        assert claimed is not None

        adapter = CountingAdapter()
        execute_one(
            claimed,
            queue=queue,
            blobs=blobs,
            store=FileArtifactStore(tempfile.mkdtemp()),  # private, per worker
            registry=registry,
            adapter=adapter,
            now=now,
        )
        calls.append(adapter.calls)

    assert calls == [1, 1], (
        "with private store roots both workers should pay; if they do not, the "
        "counter is not measuring what the test above assumes"
    )
