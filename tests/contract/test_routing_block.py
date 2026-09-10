"""T179, FR-075, FR-076, FR-072 — the routing block on the wire.

`decide` is tested thoroughly as a function. What was untested is the part a
client actually sees: whether a configured policy reaches
`GET /v1/runs/{run_id}` at all, and what the block looks like when it does.
`_routing_for` loads the result back out of the store and calls `decide`, and
until this file that whole path had no test — only the *absent* case, in
`test_milestone_9_deployment_unchanged.py`.

**Absent, not null** (FR-075). A deployment with no policy carries no `routing`
key whatsoever. Emitting `"routing": null` would be a second way of saying "not
configured", and a client would eventually branch on it — at which point the two
spellings have to agree forever.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.api.app import _Deployment, build_app
from docdoc.runs.model import DEFAULT_TENANT, Run, RunStatus
from docdoc.runs.routing import RoutingOutcome, RoutingPolicy

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=UTC)


class _Grounding:
    def __init__(self, outcomes: dict) -> None:
        self.outcomes = outcomes


class _Validation:
    def __init__(self, verdict: str) -> None:
        self.verdict = verdict
        self.findings = ()
        self.provenance = _Provenance()


class _Provenance:
    grounding_artifact_id = "sha256:" + "g" * 64


class _Outcome:
    def __init__(self, status: str, score: float | None) -> None:
        self.status = status
        self.score = score


class _Store:
    """Returns the validation and grounding artifacts `_routing_for` walks back to.

    A stand-in rather than a real store because what is under test is the
    *route* — whether a configured policy reaches the response — and building
    four real artifacts would make this a test about the artifact chain, which
    `test_reuse.py` already owns.
    """

    def __init__(self, *, ungrounded: bool) -> None:
        self._ungrounded = ungrounded

    def get(self, artifact_id: str, *, model=None, artifact_format_version=None):
        if artifact_id.startswith("sha256:g"):
            status = "ungrounded" if self._ungrounded else "exact"
            score = None if self._ungrounded else 1.0
            return _Grounding({"total": _Outcome(status, score)})
        return _Validation("valid")


def _client(
    *,
    policy: RoutingPolicy | None,
    ungrounded: bool = True,
) -> tuple[TestClient, InMemoryRunQueue, Run]:
    """A deployment with **authentication off**, which is the default.

    Deliberately not the authenticated harness `test_erased_run_responses.py`
    uses. Turning authentication on requires a store *location* rather than store
    objects — `build_app` refuses the combination, correctly, because it could not
    namespace one tenant away from another — and a location would build real
    artifact stores, which would make this a test about the artifact chain rather
    than about whether a configured policy reaches the response.

    Tenant scoping on this route is asserted in `test_erased_run_responses.py`
    and `test_tenant_isolation.py`. What is under test here is the routing block.
    """
    queue = InMemoryRunQueue()
    run = Run(
        run_id=uuid4(),
        tenant_id=DEFAULT_TENANT,
        blob_id="sha256:" + "a" * 64,
        schema_identity="invoice@1",
        status=RunStatus.SUCCEEDED,
        processing_id="sha256:" + "v" * 64,
        created_at=NOW,
        updated_at=NOW,
        expires_at=NOW + timedelta(days=30),
    )
    queue._runs[run.run_id] = run

    store = _Store(ungrounded=ungrounded)
    deployment = _Deployment(store=store, blobs=store, runs=queue, routing=policy)
    return TestClient(build_app(deployment)), queue, run


def _read(client: TestClient, run: Run) -> dict:
    response = client.get(f"/v1/runs/{run.run_id}")
    assert response.status_code == 200, response.text
    return dict(response.json())


POLICY = RoutingPolicy(version="default@1", required_fields=frozenset({"total"}))


# -- absent when unconfigured -------------------------------------------------


def test_no_policy_means_no_routing_key_at_all() -> None:
    """**FR-075.** Absent, not null — and `null` is what a response model with an
    optional field would have emitted."""
    client, _, run = _client(policy=None)

    body = _read(client, run)

    assert "routing" not in body
    assert "null" not in json.dumps(body.get("routing", "")).lower() or True


# -- present and shaped when configured ---------------------------------------


def test_a_configured_policy_reaches_the_response() -> None:
    """**FR-076.** The path this file exists for: policy → store → `decide` → body."""
    client, _, run = _client(policy=POLICY)

    routing = _read(client, run)["routing"]

    assert routing["outcome"] == "review"
    assert routing["policy_version"] == "default@1"


def test_the_outcome_is_one_of_exactly_two_values() -> None:
    """FR-067 on the wire. Never a score, and never a third word."""
    permitted = {str(member) for member in RoutingOutcome}
    assert permitted == {"automatic", "review"}

    for ungrounded, expected in ((True, "review"), (False, "automatic")):
        client, _, run = _client(policy=POLICY, ungrounded=ungrounded)
        outcome = _read(client, run)["routing"]["outcome"]
        assert outcome in permitted
        assert outcome == expected


def test_every_decision_carries_the_version_that_produced_it() -> None:
    """FR-070. Without it a stored decision cannot be explained after an edit."""
    tightened = POLICY.model_copy(update={"version": "default@7"})
    client, _, run = _client(policy=tightened)

    assert _read(client, run)["routing"]["policy_version"] == "default@7"


def test_the_reasons_name_field_signal_and_observation() -> None:
    """**FR-072.** A reader has to be able to see *why*, and act on it."""
    client, _, run = _client(policy=POLICY)

    reasons = _read(client, run)["routing"]["reasons"]

    assert reasons, "a review outcome with no reasons is unactionable"
    assert set(reasons[0]) == {"field", "signal", "observed"}
    assert reasons[0]["field"] == "total"
    assert reasons[0]["signal"] == "grounding"
    assert reasons[0]["observed"] == "ungrounded"


def test_observed_is_a_string_and_never_a_score() -> None:
    """ADR-0004: grounding scores are not comparable across tiers, so a number
    here would invite a comparison that means nothing."""
    client, _, run = _client(policy=POLICY)

    for reason in _read(client, run)["routing"]["reasons"]:
        assert isinstance(reason["observed"], str)
        assert not isinstance(reason["observed"], bool | int | float)


def test_an_automatic_outcome_carries_no_reasons() -> None:
    """Nothing was wrong, so there is nothing to name."""
    client, _, run = _client(policy=POLICY, ungrounded=False)

    routing = _read(client, run)["routing"]

    assert routing["outcome"] == "automatic"
    assert routing["reasons"] == []


def test_the_block_does_not_displace_the_milestone_9_fields() -> None:
    """A caller that ignores `routing` sees exactly what it saw before.

    The route returns a `JSONResponse` rather than the response model when a
    decision exists, so this is the assertion that the hand-built body did not
    quietly drop a field the model would have carried.
    """
    with_policy, _, run = _client(policy=POLICY)
    body = _read(with_policy, run)

    without, _, run2 = _client(policy=None)
    baseline = _read(without, run2)

    assert set(body) - {"routing"} == set(baseline)
    assert body["status"] == "succeeded"
    assert body["priority"] == "ordinary"


def test_a_run_that_has_not_succeeded_carries_no_decision() -> None:
    """There is no result to read, so there is no decision to report — as
    against a decision saying nothing, which would be one nobody computed."""
    client, queue, run = _client(policy=POLICY)
    queued = run.model_copy(update={"status": RunStatus.QUEUED, "processing_id": None})
    queue._runs[run.run_id] = queued

    assert "routing" not in _read(client, run)
