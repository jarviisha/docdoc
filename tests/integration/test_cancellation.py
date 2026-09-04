"""SC-015 — what cancelling does, and the two things it does not do.

**A queued run never executes**, in 100% of cases. That one is cheap and total.

**A running run stops before the *next* billable stage**, which is a weaker
promise and the honest one. A provider call already in flight completes and is
billed — 0% of in-flight calls are aborted (FR-029) — because the worker consults
the cancellation flag at stage boundaries and a stage boundary is, by definition,
not in the middle of a stage.

The route says so too: `DELETE /v1/runs/{id}` on a running run answers `200` with
`status: "running"`, meaning *requested* rather than *stopped*. Reporting
`cancelled` there would be the one lie this endpoint must not tell, and a caller
who saw it would reasonably conclude the work had stopped.

Driven against the in-memory queue and `execute_one` rather than a real worker
loop: the questions are about the *rule* — does a cancelled run execute, does a
running one stop at the next boundary — and a thread and a database would add
nothing but flakiness. `test_run_queue_postgres.py` holds what is genuinely about
the database.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from tests.fixtures.run_queue import InMemoryRunQueue

pytest.importorskip("pymupdf", reason="a real four-stage run is what is being cancelled")

from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.runs.errors import RunNotCancellableError
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
    """The echo adapter, counting the billable call.

    Blanket delegation, for the reason `test_shared_store_reuse.py` records: an
    adapter's cache identity is wider than the `ModelAdapter` protocol, and a
    wrapper forwarding only the protocol changes the extract stage's artifact id.
    """

    def __init__(self) -> None:
        self._inner = EchoAdapter.from_fixtures("tests/fixtures/echo")
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        return self._inner.complete(*args, **kwargs)


@pytest.fixture
def setup(tmp_path: Path):
    """A queue, a store, a blob, and a counter."""
    queue = InMemoryRunQueue()
    blobs = BlobStore(tmp_path)
    blob_id = blobs.put(FIXTURE.read_bytes())
    return queue, blobs, FileArtifactStore(tmp_path), blob_id, CountingAdapter()


def _submit(queue: InMemoryRunQueue, blob_id: str):
    return queue.submit(
        Spec(blob_id=blob_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=NOW,
        expires_at=NOW + timedelta(days=30),
    )


# -- a queued run --------------------------------------------------------------


def test_a_cancelled_queued_run_never_executes(setup) -> None:  # type: ignore[no-untyped-def]
    """SC-015's 100%. Terminal immediately, and never claimable again."""
    queue, _blobs, _store, blob_id, adapter = setup
    run = _submit(queue, blob_id)

    cancelled = queue.cancel(run.run_id, DEFAULT_TENANT, now=NOW)

    assert cancelled.status is RunStatus.CANCELLED
    assert queue.claim(worker_id="w", now=NOW, lease=DEFAULT_LEASE, max_attempts=3) is None, (
        "a cancelled run was handed to a worker; cancelling before execution has "
        "to mean it never executes"
    )
    assert adapter.calls == 0


def test_cancelling_a_cancelled_run_is_idempotent(setup) -> None:  # type: ignore[no-untyped-def]
    """FR-034. A retried cancellation is not an error."""
    queue, _blobs, _store, blob_id, _adapter = setup
    run = _submit(queue, blob_id)

    first = queue.cancel(run.run_id, DEFAULT_TENANT, now=NOW)
    second = queue.cancel(run.run_id, DEFAULT_TENANT, now=NOW)

    assert first.status is second.status is RunStatus.CANCELLED
    assert first.run_id == second.run_id


# -- a running run --------------------------------------------------------------


def test_a_running_run_stops_before_the_next_billable_stage(setup) -> None:  # type: ignore[no-untyped-def]
    """The case with the economic point, and the one the callback exists for.

    Cancelled after the claim and before execution, so the worker's first
    boundary check — the one before parse — sees it. Nothing is billed.
    """
    queue, blobs, store, blob_id, adapter = setup
    _submit(queue, blob_id)
    claimed = queue.claim(worker_id="w", now=NOW, lease=DEFAULT_LEASE, max_attempts=3)
    assert claimed is not None

    requested = queue.cancel(claimed.run_id, DEFAULT_TENANT, now=NOW)
    assert requested.status is RunStatus.RUNNING, (
        "the queue reported `cancelled` for a run still executing. A provider "
        "call in flight completes and is billed, so that would be the one lie "
        "this operation must not tell (FR-029)"
    )

    execute_one(
        claimed,
        queue=queue,
        blobs=blobs,
        store=store,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=adapter,
        now=NOW,
    )

    final = queue.get(claimed.run_id, DEFAULT_TENANT)
    assert final is not None
    assert final.status is RunStatus.CANCELLED
    assert final.processing_id is None, "a cancelled run must name no result"
    assert final.failed_stage is None, "a cancellation is not a stage failure"
    assert adapter.calls == 0, (
        f"the model was called {adapter.calls} times after cancellation; the "
        "boundary check ran too late to save anything"
    )


def test_a_stage_already_in_flight_is_never_aborted(setup) -> None:  # type: ignore[no-untyped-def]
    """SC-015's 0%, and the reason the promise is worded the way it is.

    Cancellation arrives *during* the extract stage. The call completes and is
    billed — one invocation, not zero — and the run stops at the next boundary
    rather than abandoning work already paid for.
    """
    queue, blobs, store, blob_id, adapter = setup
    _submit(queue, blob_id)
    claimed = queue.claim(worker_id="w", now=NOW, lease=DEFAULT_LEASE, max_attempts=3)
    assert claimed is not None

    inner_complete = adapter.complete

    def cancel_midway(*args: Any, **kwargs: Any) -> Any:
        """Request cancellation from inside the billable call itself."""
        queue.cancel(claimed.run_id, DEFAULT_TENANT, now=NOW)
        return inner_complete(*args, **kwargs)

    adapter.complete = cancel_midway  # type: ignore[method-assign]

    execute_one(
        claimed,
        queue=queue,
        blobs=blobs,
        store=store,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=adapter,
        now=NOW,
    )

    assert adapter.calls == 1, (
        "the in-flight provider call was abandoned. It had already been made and "
        "is already billed; abandoning it loses the result and saves nothing"
    )
    final = queue.get(claimed.run_id, DEFAULT_TENANT)
    assert final is not None
    assert final.status is RunStatus.CANCELLED
    assert final.processing_id is None
    # The stage that completed kept its artifact, so the money bought something.
    assert list((store.root / "artifacts").rglob("*.json"))


# -- a terminal run --------------------------------------------------------------


def test_cancelling_a_succeeded_run_is_refused_and_names_the_state(setup) -> None:  # type: ignore[no-untyped-def]
    """FR-031. A succeeded run has a stored result, reachable by its identity.

    Reporting it cancelled would make a retrievable result unreachable through a
    lie about its history — the caller stops asking for something that is there.
    """
    queue, blobs, store, blob_id, adapter = setup
    run = _submit(queue, blob_id)
    claimed = queue.claim(worker_id="w", now=NOW, lease=DEFAULT_LEASE, max_attempts=3)
    assert claimed is not None
    execute_one(
        claimed,
        queue=queue,
        blobs=blobs,
        store=store,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=adapter,
        now=NOW,
    )
    finished = queue.get(run.run_id, DEFAULT_TENANT)
    assert finished is not None
    assert finished.status is RunStatus.SUCCEEDED

    with pytest.raises(RunNotCancellableError) as refused:
        queue.cancel(run.run_id, DEFAULT_TENANT, now=NOW)

    assert refused.value.state == "succeeded"
    still = queue.get(run.run_id, DEFAULT_TENANT)
    assert still is not None
    assert still.status is RunStatus.SUCCEEDED
    assert still.processing_id == finished.processing_id


# -- over HTTP -------------------------------------------------------------------
#
# The route's three answers, because the statuses are a contract and the 200 on a
# running run is the one a caller will misread if it is not pinned.


@pytest.fixture
def client(setup, tmp_path: Path):  # type: ignore[no-untyped-def]
    pytest.importorskip("fastapi", reason="the HTTP interface lives behind docdoc[api]")
    from fastapi.testclient import TestClient

    from docdoc.api.app import _Deployment, build_app

    queue, _blobs, _store, _blob_id, adapter = setup
    return TestClient(
        build_app(
            _Deployment(
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=adapter,
                runs=queue,
            )
        )
    )


def test_the_route_reports_running_for_a_running_run(setup, client) -> None:  # type: ignore[no-untyped-def]
    """FR-029 as a status line. `200` means *requested*, not *stopped*.

    Pinned because a caller reading `cancelled` here would conclude the work had
    stopped, close the connection, and be wrong about their bill.
    """
    queue, _blobs, _store, blob_id, _adapter = setup
    run = _submit(queue, blob_id)
    queue.claim(worker_id="w", now=NOW, lease=DEFAULT_LEASE, max_attempts=3)

    response = client.delete(f"/v1/runs/{run.run_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "running"


def test_the_route_cancels_a_queued_run_immediately(setup, client) -> None:  # type: ignore[no-untyped-def]
    queue, _blobs, _store, blob_id, _adapter = setup
    run = _submit(queue, blob_id)

    response = client.delete(f"/v1/runs/{run.run_id}")

    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"


def test_the_route_refuses_a_terminal_run_with_409_naming_the_state(  # type: ignore[no-untyped-def]
    setup, client
) -> None:
    """FR-031. The state is in the body, so a caller need not guess which one."""
    queue, _blobs, _store, blob_id, _adapter = setup
    run = _submit(queue, blob_id)
    queue.cancel(run.run_id, DEFAULT_TENANT, now=NOW)

    from docdoc.runs.model import RunOutcome

    queue.finish(
        run.run_id, RunOutcome(status=RunStatus.FAILED, error_class="ProviderError"), now=NOW
    )
    # `finish` is a no-op on an already-terminal run, so cancel a fresh one to a
    # terminal state the route must refuse.
    other = _submit(queue, blob_id)
    queue.claim(worker_id="w", now=NOW, lease=DEFAULT_LEASE, max_attempts=3)
    queue.finish(
        other.run_id, RunOutcome(status=RunStatus.FAILED, error_class="ProviderError"), now=NOW
    )

    response = client.delete(f"/v1/runs/{other.run_id}")

    assert response.status_code == 409
    assert response.json()["error"]["class"] == "RunNotCancellableError"
    assert response.json()["error"]["detail"]["status"] == "failed"


def test_the_route_answers_404_for_another_tenants_run(setup, client) -> None:  # type: ignore[no-untyped-def]
    """FR-063, FR-066 — and the same body an unknown identifier gets."""
    queue, _blobs, _store, blob_id, _adapter = setup
    run = queue.submit(
        Spec(blob_id=blob_id, tenant_id="globex"),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=NOW,
        expires_at=NOW + timedelta(days=30),
    )

    theirs = client.delete(f"/v1/runs/{run.run_id}")
    unknown = client.delete("/v1/runs/00000000-0000-4000-8000-000000000000")

    assert theirs.status_code == unknown.status_code == 404
    assert theirs.text == unknown.text
