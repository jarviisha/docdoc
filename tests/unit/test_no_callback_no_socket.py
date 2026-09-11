"""T123a, FR-064 — a deployment that registers no callbacks opens no socket.

The import graph proves nothing here. `docdoc.runs.delivery` imports `socket` by
design and the `forbidden` contract in `pyproject.toml` is what confines that to
this one module; neither says anything about whether a module *calls* it at
runtime. FR-064 is a claim about the second.

So this is the technique `tests/unit/test_scoring_is_offline.py` already uses for
evaluation: make the call impossible and require the work to succeed anyway.
"""

from __future__ import annotations

import socket
from datetime import UTC, datetime, timedelta

import pytest

from docdoc.runs import maintenance, retention
from docdoc.runs.model import RunOutcome, RunStatus
from tests.fixtures.run_queue import InMemoryRunQueue

AT = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


@pytest.fixture
def no_sockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Every way out of the process, refused.

    Both `socket.socket` and `socket.create_connection`: the second does not go
    through the first on every platform, and a guard that covered one would be a
    guard an implementation could walk around without anybody noticing.
    """

    def _refuse(*_: object, **__: object):
        raise AssertionError(
            "an outbound connection was attempted by a deployment that has "
            "registered no callbacks (FR-064)"
        )

    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)


class _Spec:
    tenant_id = "acme"
    blob_id = "sha256:" + "a" * 64
    schema_identity = "invoice@3"
    request_id = None
    idempotency_key = None


def test_a_full_submit_claim_finish_cycle_opens_nothing(no_sockets: None) -> None:
    """The cycle FR-064 is about, with the network made impossible."""
    from uuid import uuid4

    queue = InMemoryRunQueue()
    run = queue.submit(_Spec(), run_id=uuid4(), now=AT, expires_at=AT + timedelta(days=30))

    claimed = queue.claim(worker_id="w1", now=AT, lease=timedelta(seconds=90), max_attempts=3)
    assert claimed is not None
    assert claimed.run_id == run.run_id

    queue.finish(
        run.run_id,
        RunOutcome(status=RunStatus.SUCCEEDED, processing_id="sha256:" + "b" * 64),
        now=AT,
        worker_id="w1",
    )

    assert queue.get(run.run_id, "acme").status is RunStatus.SUCCEEDED


def test_a_maintenance_tick_with_no_deliverer_opens_nothing(no_sockets: None) -> None:
    """The other half: the tick is where a delivery *would* be attempted.

    With `deliverer=None` — which is the default and what a deployment that
    configured no signing secret gets — the step is skipped rather than entered
    and found empty.
    """
    queue = InMemoryRunQueue()

    report = maintenance.tick(
        maintenance.MaintenanceDeps(
            queue=queue,
            stores_for=lambda _: retention.Stores(artifacts=_Nothing(), blobs=_Nothing()),
        ),
        now=AT,
        retention_period=timedelta(days=30),
        batch=10,
    )

    assert report.attempted == 0
    assert report.delivered == 0


class _Nothing:
    """A store holding nothing, which is all the sweep needs to find nothing."""

    def delete(self, identity: str) -> bool:
        return False

    def delete_prefix(self, *, allow_store_root: bool = False) -> int:
        return 0


def test_the_socket_guard_can_actually_fire(no_sockets: None) -> None:
    """Guards the guard. A monkeypatch that missed would make this file vacuous."""
    with pytest.raises(AssertionError):
        socket.create_connection(("127.0.0.1", 9))
