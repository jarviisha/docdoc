"""SC-016 — one key twice under one tenant is one run; under two, it is two.

Both halves matter and they fail in opposite directions. Scoping the key too
narrowly makes a client's retry a second run, and the client pays twice for a
document it submitted once. Scoping it too widely lets one tenant's choice of
`Idempotency-Key` suppress another tenant's submission — a denial of service one
header long, available to anyone who guesses that the other tenant uses `invoice-1`.

Asserted against the in-memory queue, which is what the protocol exists for: this
is a question about the *rule*, and the composite key is the same rule whether a
partial unique index or a dictionary enforces it. `test_run_queue_postgres.py`
holds the half that is genuinely about the database — that two API processes
racing on one client's retry cannot both insert.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from docdoc.runs.identity import new_run_id
from docdoc.runs.model import DEFAULT_TENANT
from tests.fixtures.run_queue import InMemoryRunQueue

NOW = datetime(2026, 9, 3, 12, 0, tzinfo=UTC)
LATER = NOW + timedelta(days=30)


@dataclass
class Spec:
    tenant_id: str = DEFAULT_TENANT
    blob_id: str = "sha256:" + "a" * 64
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None


def _submit(queue: InMemoryRunQueue, **fields: object):
    return queue.submit(
        Spec(**fields),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=NOW,
        expires_at=LATER,
    )


def test_one_key_twice_under_one_tenant_is_one_run() -> None:
    """FR-011. The retry a client did not mean to be a second submission."""
    queue = InMemoryRunQueue()

    first = _submit(queue, tenant_id="acme", idempotency_key="invoice-1")
    second = _submit(queue, tenant_id="acme", idempotency_key="invoice-1")

    assert first.run_id == second.run_id
    assert second.created_at == first.created_at, (
        "the second submission returned a different run's timestamps, so it "
        "created one rather than finding the original"
    )


def test_the_same_key_under_two_tenants_is_two_runs() -> None:
    """SC-016, and the denial of service the composite key closes.

    Without the tenant in the key, `acme` submitting with `invoice-1` would make
    `globex`'s `invoice-1` return acme's run — a cross-tenant read *and* a
    suppressed submission, from a header anyone can guess.
    """
    queue = InMemoryRunQueue()

    acme = _submit(queue, tenant_id="acme", idempotency_key="invoice-1")
    globex = _submit(queue, tenant_id="globex", idempotency_key="invoice-1")

    assert acme.run_id != globex.run_id
    assert acme.tenant_id == "acme"
    assert globex.tenant_id == "globex"


def test_a_run_created_under_one_tenants_key_is_not_readable_by_the_other() -> None:
    """The consequence, spelled out: two runs, and neither sees the other."""
    queue = InMemoryRunQueue()
    acme = _submit(queue, tenant_id="acme", idempotency_key="invoice-1")

    assert queue.get(acme.run_id, "globex") is None
    assert queue.get(acme.run_id, "acme") is not None


def test_no_key_means_no_deduplication() -> None:
    """FR-005. Two deliberate resubmissions are two runs.

    This is why the key is not derived from the document's hash: a derived key
    would silently merge two submissions that a caller made on purpose, and
    there would be no way to ask for the second one.
    """
    queue = InMemoryRunQueue()

    first = _submit(queue, tenant_id="acme")
    second = _submit(queue, tenant_id="acme")

    assert first.run_id != second.run_id


def test_two_different_keys_under_one_tenant_are_two_runs() -> None:
    """Guards the guard: a queue that deduplicated everything would pass above."""
    queue = InMemoryRunQueue()

    first = _submit(queue, tenant_id="acme", idempotency_key="invoice-1")
    second = _submit(queue, tenant_id="acme", idempotency_key="invoice-2")

    assert first.run_id != second.run_id


# -- the status code, at the HTTP boundary (T098) -----------------------------
#
# Everything above asks the queue. The queue was right the whole time and the
# route was not: it returned `202` for both cases, so a client could not tell
# "accepted, work queued" from "this is the run you already had". That survived
# because this file never crossed the HTTP boundary — the rule was tested and the
# transport was not.

import pytest  # noqa: E402

from tests.support import KEYS_FILE, bearer  # noqa: E402

FIXTURE = "tests/fixtures/pdf/digital_invoice.pdf"
SCHEMA = "invoice@1"


@pytest.fixture
def client(tmp_path):  # type: ignore[no-untyped-def]
    pytest.importorskip("fastapi", reason="the HTTP interface lives behind docdoc[api]")
    from pathlib import Path

    from fastapi.testclient import TestClient

    from docdoc.api.app import _Deployment, build_app
    from docdoc.api.auth import KeyRing
    from docdoc.extraction import SchemaRegistry
    from docdoc.extraction.adapters.echo import EchoAdapter

    return TestClient(
        build_app(
            _Deployment(
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                runs=InMemoryRunQueue(),
                keys=KeyRing.from_file(Path(KEYS_FILE)),
            )
        )
    )


def _blob(client, tenant: str) -> str:  # type: ignore[no-untyped-def]
    from pathlib import Path

    response = client.post(
        "/v1/documents", content=Path(FIXTURE).read_bytes(), headers=bearer(tenant)
    )
    assert response.status_code == 200, response.text
    return str(response.json()["blob_id"])


def _submit_run(client, tenant: str, blob_id: str, key: str | None = None):  # type: ignore[no-untyped-def]
    headers = dict(bearer(tenant))
    if key is not None:
        headers["Idempotency-Key"] = key
    return client.post(f"/v1/documents/{blob_id}/runs", params={"schema": SCHEMA}, headers=headers)


def test_a_new_run_is_202_and_a_replay_is_200(client) -> None:  # type: ignore[no-untyped-def]
    """contracts/runs-http-api.md's two rows, which the route collapsed into one.

    The distinction is not decorative. A client retrying through a flaky network
    sent the key precisely to learn whether its first attempt landed, and a
    single status code answers "here is a run" without answering that.
    """
    blob_id = _blob(client, "acme")

    first = _submit_run(client, "acme", blob_id, key="retry-1")
    replay = _submit_run(client, "acme", blob_id, key="retry-1")

    assert first.status_code == 202, "a run that was created must say so"
    assert replay.status_code == 200, (
        "the replay answered 202, so a client cannot tell a queued run from one "
        "that already existed — which is the whole reason it sent a key"
    )
    assert first.json()["run_id"] == replay.json()["run_id"]
    assert first.json() == replay.json(), "the body must be the original run, unchanged"


def test_a_submission_with_no_key_is_always_202(client) -> None:  # type: ignore[no-untyped-def]
    """FR-005: two deliberate resubmissions are two runs, and both are new."""
    blob_id = _blob(client, "acme")

    first = _submit_run(client, "acme", blob_id)
    second = _submit_run(client, "acme", blob_id)

    assert first.status_code == second.status_code == 202
    assert first.json()["run_id"] != second.json()["run_id"]


def test_the_same_key_under_two_tenants_is_202_for_both(client) -> None:  # type: ignore[no-untyped-def]
    """SC-016 at the boundary, and the denial of service it closes.

    Without the tenant in the key, `globex`'s submission would come back `200`
    naming `acme`'s run — a cross-tenant read *and* a suppressed submission, from
    a header anyone can guess. Both must be 202, and the ids must differ.
    """
    acme_blob = _blob(client, "acme")
    globex_blob = _blob(client, "globex")

    acme = _submit_run(client, "acme", acme_blob, key="invoice-1")
    globex = _submit_run(client, "globex", globex_blob, key="invoice-1")

    assert acme.status_code == globex.status_code == 202
    assert acme.json()["run_id"] != globex.json()["run_id"]
