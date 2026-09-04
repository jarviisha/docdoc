"""SC-017 — the half of isolation a status code cannot deliver.

`test_tenant_isolation.py` asserts that cross-tenant *responses* are identical to
non-existence. That is necessary and it is not enough, and the reason is a
property this project spent eight milestones building on purpose: **identity is
derived from content.** Two tenants who submit the same invoice against the same
schema arrive at the same `blob_id`, the same `artifact_id`, and the same
`processing_id`, independently and unavoidably.

So over a shared store, a tenant learns that another tenant holds a document by
submitting it and observing that the result comes back immediately and costs
nothing. Latency is not a response body and a provider invoice is not a status
code, so a design that scoped only at the read boundary would satisfy every
assertion in that file and leak anyway.

**This is the test the namespacing decision exists for.** It is measured on
invocation counters rather than on a stopwatch, for the reason
`test_shared_store_reuse.py` gives: a timing assertion is flaky on a slow machine
and, far worse, *passes* on a fast one that was re-parsing every document.

The price is recorded rather than hidden: two tenants with the same document pay
for two parses and two extractions (ADR-0014 §4). Reuse **within** a tenant is
untouched, and the last test here pins that, because a change that closed the
oracle by disabling reuse altogether would pass everything above it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.support import KEYS_FILE, bearer

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")
pytest.importorskip("pymupdf", reason="a real parse is what is being counted")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.api.auth import KeyRing
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"


class CountingAdapter:
    """The echo adapter, with a counter on the billable call.

    Blanket delegation rather than an enumerated list of forwarded attributes,
    for the reason `test_shared_store_reuse.py` records at length: an adapter's
    *cache identity* is wider than the `ModelAdapter` protocol —
    `pipeline/plan.py` reads `model_id` and `model_version` to build the extract
    stage's `options_hash` — so a wrapper forwarding only the protocol produces a
    different artifact id, and the test reports a reuse failure that exists only
    in its own double.
    """

    def __init__(self) -> None:
        self._inner = EchoAdapter.from_fixtures("tests/fixtures/echo")
        self.calls = 0

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def complete(self, *args: Any, **kwargs: Any) -> Any:
        """`complete`, not `extract`: that is the `ModelAdapter` protocol.

        Naming it wrongly would let the pipeline go past this object to the inner
        adapter, leaving the counter at zero and every assertion below passing
        for the worst possible reason.
        """
        self.calls += 1
        return self._inner.complete(*args, **kwargs)


@pytest.fixture
def counted(tmp_path: Path) -> tuple[TestClient, CountingAdapter]:
    """Two tenants, one store root, and a counter on the model call."""
    adapter = CountingAdapter()
    deployment = _Deployment(
        store_root=tmp_path,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=adapter,
        keys=KeyRing.from_file(Path(KEYS_FILE)),
    )
    return TestClient(build_app(deployment)), adapter


def _process(client: TestClient, tenant: str) -> Any:
    """Submit and extract the same document as one tenant."""
    submitted = client.post("/v1/documents", content=FIXTURE.read_bytes(), headers=bearer(tenant))
    assert submitted.status_code == 200, submitted.text
    blob_id = submitted.json()["blob_id"]
    result = client.post(
        f"/v1/documents/{blob_id}/extract",
        params={"schema": SCHEMA},
        headers=bearer(tenant),
    )
    assert result.status_code == 200, result.text
    return result


def test_a_second_tenant_pays_exactly_what_a_first_ever_submission_pays(
    counted: tuple[TestClient, CountingAdapter],
) -> None:
    """SC-017. The criterion the store's namespacing exists to satisfy.

    Equality, not "greater than zero". A second tenant paying *some* of the cost
    would still leak — the oracle is a difference, and any difference is one.
    """
    client, adapter = counted

    _process(client, "acme")
    first_ever = adapter.calls
    assert first_ever > 0, "the counter never moved; this test is measuring nothing"

    adapter.calls = 0
    _process(client, "globex")
    second_tenant = adapter.calls

    assert second_tenant == first_ever, (
        f"the second tenant paid {second_tenant} model calls where a first-ever "
        f"submission pays {first_ever}. The difference is an existence oracle: a "
        f"tenant learns which documents another holds by submitting one and "
        f"watching the bill"
    )


def test_the_same_tenant_twice_reuses_everything(
    counted: tuple[TestClient, CountingAdapter],
) -> None:
    """FR-086's other half, and the guard on the test above.

    Closing the oracle by disabling reuse altogether would satisfy every
    assertion in this file except this one. Reuse *within* a tenant is the case
    ADR-0003 was written for — change a prompt, keep the parse — and it is not
    what was traded away.
    """
    client, adapter = counted

    _process(client, "acme")
    adapter.calls = 0
    _process(client, "acme")

    assert adapter.calls == 0, (
        "the same tenant re-paid for a document it had already processed; "
        "per-tenant namespacing was supposed to cost cross-tenant reuse and "
        "nothing else"
    )


def test_the_two_tenants_still_derive_the_same_result(
    counted: tuple[TestClient, CountingAdapter],
) -> None:
    """FR-085 and FR-051, from the direction that matters for billing.

    Both tenants pay in full *and* get the same answer under the same identity.
    If the results diverged, the extra cost would have bought something, and the
    ADR's claim that this changes location and never identity would be false.
    """
    client, _ = counted

    acme = _process(client, "acme").json()
    globex = _process(client, "globex").json()

    assert acme["job_id"] == globex["job_id"]
    assert acme["validation"] == globex["validation"]
    assert acme["extraction"] == globex["extraction"]
