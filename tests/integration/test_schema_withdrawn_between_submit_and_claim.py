"""FR-091 — a withdrawn schema is a configuration fault, not a poison document.

The gap this closes is a real one and it is invisible from either end alone. A
run is accepted while `invoice@1` resolves; by the time a worker claims it, the
schema has been removed from the registry — an operator rotated a mount, a config
map changed, a deployment rolled with a different `DOCDOC_SCHEMA_PATHS`. The run
can never succeed, and nothing about it is transient.

Without FR-091 the worker would crash or fail, the lease would lapse, the run
would be redelivered, and after three attempts it would come to rest as
`RunAbandonedError` — **the word that tells an operator to go and look at the
document.** They would look at a perfectly good invoice, three times, while the
actual fault sits in a mount path.

So the schema is resolved *before* the pipeline is called, and a failure there is
terminal on the first claim:

* `status: failed`, with a schema error class;
* `failed_stage: null`, because the run reached no stage — that is a real
  distinction and not a missing value;
* claimed exactly **once**, and never reported as abandoned (FR-038).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
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
MAX_ATTEMPTS = 3


@dataclass
class Spec:
    blob_id: str
    tenant_id: str = DEFAULT_TENANT
    schema_identity: str = SCHEMA
    request_id: str | None = None
    idempotency_key: str | None = None


class WithdrawnRegistry:
    """A registry that has the schema at submission and not at claim.

    Modelled as a registry that refuses rather than as an empty one, because the
    *shape* being tested is "resolve raised" — an empty registry raises a
    different, friendlier error (`docdoc.cli.config.empty_registry_message`
    exists for exactly that case) and would exercise a different path.
    """

    def __init__(self) -> None:
        self._real = SchemaRegistry.from_paths([Path("schemas")])
        self.withdrawn = False
        self.resolutions = 0

    def identities(self) -> Any:
        return () if self.withdrawn else self._real.identities()

    def resolve(self, identity: str) -> Any:
        self.resolutions += 1
        if self.withdrawn:
            from docdoc.extraction.errors import SchemaError

            raise SchemaError(f"{identity} is not configured here")
        return self._real.resolve(identity)


class NeverCalledAdapter:
    """A model adapter that fails the test if it is reached.

    The run must not get as far as a provider. An adapter that quietly returned
    something would let the test pass while the run had cost money.
    """

    id = "never"
    version = "0"
    model_id = "never"
    model_version = "0"

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        raise AssertionError(
            "the model was called for a run whose schema no longer resolves; the "
            "resolution check runs too late to save anything"
        )


@pytest.fixture
def setup(tmp_path: Path):
    queue = InMemoryRunQueue()
    blobs = BlobStore(tmp_path)
    blob_id = blobs.put(FIXTURE.read_bytes())
    return queue, blobs, FileArtifactStore(tmp_path), blob_id, WithdrawnRegistry()


def test_the_run_fails_terminally_with_a_schema_error_and_no_stage(setup) -> None:  # type: ignore[no-untyped-def]
    """FR-091's three claims, in one run."""
    queue, blobs, store, blob_id, registry = setup
    submitted = queue.submit(
        Spec(blob_id=blob_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=NOW,
        expires_at=NOW + timedelta(days=30),
    )

    # Between submission and claim, the schema goes away.
    registry.withdrawn = True

    claimed = queue.claim(worker_id="w", now=NOW, lease=DEFAULT_LEASE, max_attempts=MAX_ATTEMPTS)
    assert claimed is not None
    result = execute_one(
        claimed,
        queue=queue,
        blobs=blobs,
        store=store,
        registry=registry,
        adapter=NeverCalledAdapter(),
        now=NOW,
    )

    assert result is None, "the pipeline was called although the schema was gone"

    final = queue.get(submitted.run_id, DEFAULT_TENANT)
    assert final is not None
    assert final.status is RunStatus.FAILED
    assert final.error_class == "SchemaError", (
        f"the run rests at {final.error_class!r}; an operator needs to be sent to "
        f"the configuration, not to the document"
    )
    assert final.failed_stage is None, (
        "a stage was named for a run that reached none. `null` here is a real "
        "distinction: the run never started, so no stage can be at fault"
    )
    assert final.stage_outcomes == ()
    assert final.processing_id is None


def test_it_is_claimed_exactly_once_and_never_reported_as_abandoned(setup) -> None:  # type: ignore[no-untyped-def]
    """FR-038, and the whole reason this path exists separately.

    Retrying a configuration fault three times spends nothing and buys nothing,
    and the word it eventually produces — `RunAbandonedError` — sends an operator
    to look at a perfectly good invoice.
    """
    queue, blobs, store, blob_id, registry = setup
    submitted = queue.submit(
        Spec(blob_id=blob_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    registry.withdrawn = True

    claims = 0
    at = NOW
    for _ in range(MAX_ATTEMPTS + 2):
        claimed = queue.claim(worker_id="w", now=at, lease=DEFAULT_LEASE, max_attempts=MAX_ATTEMPTS)
        if claimed is None:
            at = at + DEFAULT_LEASE + timedelta(seconds=1)
            continue
        claims += 1
        execute_one(
            claimed,
            queue=queue,
            blobs=blobs,
            store=store,
            registry=registry,
            adapter=NeverCalledAdapter(),
            now=at,
        )
        at = at + DEFAULT_LEASE + timedelta(seconds=1)

    assert claims == 1, (
        f"the run was claimed {claims} times. A withdrawn schema is not a "
        f"transient fault, and retrying it costs three workers' attention to "
        f"reach the same answer"
    )

    final = queue.get(submitted.run_id, DEFAULT_TENANT)
    assert final is not None
    assert final.error_class != "RunAbandonedError", (
        "a configuration fault was reported with the word reserved for a poison document (FR-091)"
    )
    assert final.attempts == 1


def test_a_resolvable_schema_still_runs(setup) -> None:  # type: ignore[no-untyped-def]
    """Guards both tests above: a registry that always refused would satisfy them."""
    pytest.importorskip("pymupdf")
    queue, blobs, store, blob_id, registry = setup
    submitted = queue.submit(
        Spec(blob_id=blob_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=NOW,
        expires_at=NOW + timedelta(days=30),
    )

    claimed = queue.claim(worker_id="w", now=NOW, lease=DEFAULT_LEASE, max_attempts=MAX_ATTEMPTS)
    assert claimed is not None
    execute_one(
        claimed,
        queue=queue,
        blobs=blobs,
        store=store,
        registry=registry,
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        now=NOW,
    )

    final = queue.get(submitted.run_id, DEFAULT_TENANT)
    assert final is not None
    assert final.status is RunStatus.SUCCEEDED
