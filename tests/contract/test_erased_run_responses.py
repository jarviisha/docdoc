"""T177, SC-005, FR-011 — `410` to the owner, `404` to everyone else.

**The two responses must differ in kind, and that is the whole requirement.** A
tenant's own erased run is knowable to that tenant: it was here, it is gone, and
here is when and under which policy. To anybody else it must be
indistinguishable from an identifier that never existed — because a `410` to a
stranger is an existence oracle, and Milestone 9's FR-066 spent a lot of care
making sure this interface has none.

**This file exists because a consolidation lost it.** T064 named it, Phase 4
folded its neighbours into `tests/integration/test_erasure.py`, and the HTTP
half went with them: `run_erased` appeared in no test in the repository.
`test_tombstone_holds_four_fields.py` checks the *model* and
`test_erasure.py` checks the *store*, so both halves of SC-005 were verified by
hand against a live deployment on 2026-09-09 and by nothing that runs in CI.

The `404` comparison is on **bytes**, with `==` over the raw body rather than a
field-by-field reading. "Byte-identical to an identifier that never existed" is
the claim; comparing parsed JSON would pass an implementation that emitted the
same fields in a different order, and an attacker reads bytes.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.api.app import _Deployment, build_app
from docdoc.runs.model import Run, RunStatus
from docdoc.runs.principal import digest_of
from docdoc.runs.retention import POLICY_ERASE_TENANT, POLICY_RETENTION

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)

OWNER_KEY = "owner-key"
STRANGER_KEY = "stranger-key"

#: A well-formed identity nothing ever issued. The `404` for the erased run must
#: be byte-identical to the `404` for this.
NEVER_EXISTED = UUID("00000000-0000-4000-8000-000000000000")


@pytest.fixture
def queue() -> InMemoryRunQueue:
    return InMemoryRunQueue()


@pytest.fixture
def client(tmp_path: Path, queue: InMemoryRunQueue, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Two tenants, so "another tenant's" is a real caller rather than a mock."""
    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps(
            {
                "keys": [
                    {"sha256": digest_of(OWNER_KEY), "tenant_id": "acme"},
                    {"sha256": digest_of(STRANGER_KEY), "tenant_id": "globex"},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))
    return TestClient(build_app(_Deployment(runs=queue)))


def _erased_run(queue: InMemoryRunQueue, *, policy: str = POLICY_RETENTION) -> UUID:
    """A run that existed, was removed, and left a tombstone behind."""
    run = Run(
        run_id=uuid4(),
        tenant_id="acme",
        blob_id="sha256:" + "a" * 64,
        schema_identity="invoice@1",
        status=RunStatus.SUCCEEDED,
        processing_id="sha256:" + "b" * 64,
        created_at=NOW - timedelta(days=40),
        updated_at=NOW - timedelta(days=40),
        expires_at=NOW - timedelta(days=1),
    )
    queue._runs[run.run_id] = run
    removed = queue.entomb([run], now=NOW, policy=policy)
    assert removed == 1
    return run.run_id


def _get(client: TestClient, run_id: UUID, key: str):
    return client.get(f"/v1/runs/{run_id}", headers={"Authorization": f"Bearer {key}"})


# -- the owner is told -------------------------------------------------------


def test_the_owner_is_told_it_was_here_and_is_gone(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """**SC-005, first half.** `410 Gone`, with when and under which rule."""
    run_id = _erased_run(queue)

    response = _get(client, run_id, OWNER_KEY)

    assert response.status_code == 410
    body = response.json()
    assert body["error"] == "run_erased"
    assert body["run_id"] == str(run_id)
    assert body["policy"] == POLICY_RETENTION
    # A real instant, not a placeholder: the owner is entitled to know when.
    assert datetime.fromisoformat(body["deleted_at"]) == NOW


def test_the_policy_distinguishes_ageing_out_from_being_erased(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """This field is why `RunStatus` gains no member (FR-004a).

    "It aged out" and "somebody asked for it to go" are different answers to the
    same question, and the tombstone is the only place either is recorded.
    """
    aged = _erased_run(queue, policy=POLICY_RETENTION)
    asked = _erased_run(queue, policy=POLICY_ERASE_TENANT)

    assert _get(client, aged, OWNER_KEY).json()["policy"] == "retention"
    assert _get(client, asked, OWNER_KEY).json()["policy"] == "erasure:tenant"


def test_the_410_carries_nothing_the_run_held(client: TestClient, queue: InMemoryRunQueue) -> None:
    """A tombstone holds four fields precisely so that being told about one
    discloses nothing about what it held."""
    body = _get(client, _erased_run(queue), OWNER_KEY).json()

    assert set(body) == {"error", "run_id", "deleted_at", "policy"}
    for absent in ("blob_id", "schema_identity", "processing_id", "stage_outcomes", "status"):
        assert absent not in body


# -- everybody else is told nothing ------------------------------------------


def test_another_tenant_gets_404(client: TestClient, queue: InMemoryRunQueue) -> None:
    """**SC-005, second half.** Not a `410`, and not a different `404`."""
    response = _get(client, _erased_run(queue), STRANGER_KEY)

    assert response.status_code == 404


def test_the_404_is_byte_identical_to_one_that_never_existed(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """**The assertion this file exists for.**

    Compared on the raw body rather than on parsed JSON: an implementation that
    emitted the same fields in a different order would pass a field-by-field
    reading and still be an oracle, because an attacker reads bytes.
    """
    erased = _get(client, _erased_run(queue), STRANGER_KEY)
    phantom = _get(client, NEVER_EXISTED, STRANGER_KEY)

    assert erased.status_code == phantom.status_code == 404
    assert erased.content == phantom.content, (
        "a stranger can tell an erased run from one that never existed. That is "
        "an existence oracle, and Milestone 9's FR-066 exists to deny it"
    )


def test_a_malformed_identifier_answers_the_same_way(client: TestClient) -> None:
    """The third input that must not be distinguishable.

    Telling a caller which identifiers are well-formed enough to exist is a
    smaller leak than the one above and the same kind.
    """
    malformed = client.get(
        "/v1/runs/not-a-uuid", headers={"Authorization": f"Bearer {STRANGER_KEY}"}
    )
    phantom = _get(client, NEVER_EXISTED, STRANGER_KEY)

    assert malformed.status_code == 404
    assert malformed.content == phantom.content


def test_the_owner_and_the_stranger_differ_in_kind(
    client: TestClient, queue: InMemoryRunQueue
) -> None:
    """FR-011's actual words: the two responses differ **in kind**, not in a
    field of one shape. Different status, and different body shape."""
    run_id = _erased_run(queue)

    owner = _get(client, run_id, OWNER_KEY)
    stranger = _get(client, run_id, STRANGER_KEY)

    assert owner.status_code != stranger.status_code
    assert set(owner.json()) != set(stranger.json())


def test_this_check_can_actually_fail(client: TestClient, queue: InMemoryRunQueue) -> None:
    """Guards the guard.

    Every assertion above would hold vacuously against a deployment where the
    run never existed in the first place, so: a *live* run must still answer
    `200` to its owner through this same client.
    """
    live = Run(
        run_id=uuid4(),
        tenant_id="acme",
        blob_id="sha256:" + "c" * 64,
        schema_identity="invoice@1",
        status=RunStatus.QUEUED,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    queue._runs[live.run_id] = live

    assert _get(client, live.run_id, OWNER_KEY).status_code == 200
    assert _get(client, live.run_id, STRANGER_KEY).status_code == 404
