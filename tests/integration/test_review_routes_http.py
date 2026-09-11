"""The five routes this milestone added, driven over HTTP.

**They had no automated coverage at all.** `POST /v1/runs/{id}/corrections`,
`GET /v1/runs/{id}/corrections`, `POST /v1/callbacks`,
`DELETE /v1/callbacks/{id}` and `GET /v1/runs/{id}/delivery` were exercised by
hand against a live composition on 2026-09-09 and by nothing that runs in CI:
`test_corrections_postgres.py` drives `PostgresCorrectionStore` and
`test_delivery_postgres.py` drives `PostgresDeliverer`, both below the HTTP
layer, so every tenant check, every status code, and every field name in the
responses was unverified by the thing that gates merges. That is the same shape
of gap `test_erased_run_responses.py` exists for, and it is how the `priority`
label bug and the `_routing_for` 500 both reached a live deployment.

**Against a real database rather than a fake queue.** These routes reach their
stores through ``getattr(runs, "_execute")`` — a deployment whose queue has no
such attribute gets `None` back and the routes answer "unconfigured", so an
in-memory double would exercise the refusal path and none of the others. The
`postgres` marker is the honest statement of that dependency.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from tests.infra import require_database

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.runs import migrations
from docdoc.runs.identity import DELIVERY_SECRETS_FILE_ENV, new_run_id
from docdoc.runs.model import RunOutcome, RunStatus, StageOutcomeRecord
from docdoc.runs.postgres import PostgresRunQueue
from docdoc.runs.principal import digest_of

pytestmark = pytest.mark.postgres

TENANT = "acme"
OTHER = "globex"

OWNER_KEY = "owner-key"
STRANGER_KEY = "stranger-key"

PROCESSING_ID = "sha256:" + "b" * 64
ARTIFACT_ID = "sha256:" + "c" * 64
BLOB_ID = "sha256:" + "a" * 64

#: `localhost`, because registration *resolves* the destination and a name that
#: does not resolve is refused before the policy is consulted — which is what
#: `receiver.invalid.example` turned out to do. It resolves privately, so the
#: fixtures below relax the policy deliberately and one test keeps the unrelaxed
#: refusal honest. Nothing is connected to: no attempt happens here (FR-064).
CALLBACK_URL = "https://localhost:9999/hook"

SECRET = "s" * 32


@dataclass
class Spec:
    """The submission shape `PostgresRunQueue.submit` reads."""

    tenant_id: str = TENANT
    blob_id: str = BLOB_ID
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None
    priority: int = 0
    callback_id: object = None


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs, corrections, run_tombstones, deliveries, callbacks")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


@pytest.fixture
def client(tmp_path, queue: PostgresRunQueue, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """Two tenants and a signing secret, so every route is reachable.

    The secret file is what makes `deployment.deliverer()` return something: a
    deployment with nothing to sign with registers no callbacks at all, which is
    FR-064's behaviour and is covered by its own test below.
    """
    from docdoc.api.app import _Deployment, build_app

    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps(
            {
                "keys": [
                    {"sha256": digest_of(OWNER_KEY), "tenant_id": TENANT},
                    {"sha256": digest_of(STRANGER_KEY), "tenant_id": OTHER},
                ]
            }
        ),
        encoding="utf-8",
    )
    secrets = tmp_path / "delivery-secrets.json"
    secrets.write_text(json.dumps({"secrets": [SECRET]}), encoding="utf-8")

    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))
    monkeypatch.setenv(DELIVERY_SECRETS_FILE_ENV, str(secrets))
    # Every hostname a test can name resolves privately or not at all, so the
    # SSRF policy is relaxed here on purpose. `test_a_private_destination_is_
    # refused_when_the_policy_is_not_relaxed` runs without this.
    monkeypatch.setenv("DOCDOC_DELIVERY_ALLOW_PRIVATE", "1")
    return TestClient(build_app(_Deployment(runs=queue)))


def _headers(key: str = OWNER_KEY) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}"}


def _correction_body(field: str = "total", annotator: str = "ana") -> dict[str, Any]:
    return {
        "report_id": PROCESSING_ID,
        "document_id": BLOB_ID,
        "field_path": field,
        "predicted_value": "1234.56",
        "corrected_value": "1243.56",
        "reason": "the parser transposed two digits",
        "annotator": annotator,
        "timestamp": "2026-09-09T10:00:00+00:00",
    }


def _run(
    queue: PostgresRunQueue,
    *,
    finished: bool = True,
    callback_id: object = None,
    tenant_id: str = TENANT,
):
    at = datetime.now(UTC)
    run = queue.submit(
        Spec(tenant_id=tenant_id, callback_id=callback_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=at,
        expires_at=at + timedelta(days=30),
    )
    if not finished:
        return run
    queue.finish(
        run.run_id,
        RunOutcome(
            status=RunStatus.SUCCEEDED,
            processing_id=PROCESSING_ID,
            stage_outcomes=(
                StageOutcomeRecord(stage="validate", status="executed", artifact_id=ARTIFACT_ID),
            ),
        ),
        now=at,
    )
    return queue.get(run.run_id, tenant_id)


# -- POST /v1/runs/{run_id}/corrections --------------------------------------


def test_a_correction_is_recorded_and_answers_with_its_identity(
    client: TestClient, queue: PostgresRunQueue
) -> None:
    """FR-078. `201`, and the four fields a reviewer's client needs back."""
    run = _run(queue)

    response = client.post(
        f"/v1/runs/{run.run_id}/corrections", json=_correction_body(), headers=_headers()
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["run_id"] == str(run.run_id)
    assert body["field_path"] == "total"
    assert body["correction_id"]
    # FR-013: the correction outlives the result it annotates, or matches it.
    assert datetime.fromisoformat(body["expires_at"]) >= run.expires_at


def test_the_annotator_comes_from_the_body_and_not_the_credential(
    client: TestClient, queue: PostgresRunQueue
) -> None:
    """FR-085, and the reason it is not a convenience.

    One operations key posting every reviewer's corrections is the ordinary
    deployment. A route that stamped the credential's tenant as the annotator
    would record `acme` reviewed it, which is true of nobody.
    """
    run = _run(queue)
    client.post(
        f"/v1/runs/{run.run_id}/corrections",
        json=_correction_body(annotator="priya"),
        headers=_headers(),
    )

    listed = client.get(f"/v1/runs/{run.run_id}/corrections", headers=_headers()).json()

    assert [c["annotator"] for c in listed["corrections"]] == ["priya"]


def test_correcting_another_tenants_run_is_a_404(
    client: TestClient, queue: PostgresRunQueue
) -> None:
    """FR-079. Not a `403`: telling a stranger the run exists is the leak."""
    run = _run(queue)

    response = client.post(
        f"/v1/runs/{run.run_id}/corrections",
        json=_correction_body(),
        headers=_headers(STRANGER_KEY),
    )

    assert response.status_code == 404


def test_correcting_an_unfinished_run_is_a_409(client: TestClient, queue: PostgresRunQueue) -> None:
    """There is no result to correct yet, and that is not the caller's mistake."""
    run = _run(queue, finished=False)

    response = client.post(
        f"/v1/runs/{run.run_id}/corrections", json=_correction_body(), headers=_headers()
    )

    assert response.status_code == 409
    assert response.json()["error"] == "run_has_no_result"


def test_a_malformed_correction_names_the_class_and_not_the_value(
    client: TestClient, queue: PostgresRunQueue
) -> None:
    """A validation message quoting the body would republish document content."""
    run = _run(queue)
    typed = "0118 999 881 999 119 725 3"

    response = client.post(
        f"/v1/runs/{run.run_id}/corrections",
        json={"field_path": "total", "corrected_value": typed},
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"] == "invalid_correction"
    assert typed not in response.text


def test_a_malformed_run_identifier_is_a_404(client: TestClient) -> None:
    response = client.post(
        "/v1/runs/not-a-uuid/corrections", json=_correction_body(), headers=_headers()
    )

    assert response.status_code == 404


def test_correcting_an_erased_run_is_a_410_naming_the_policy(
    client: TestClient, queue: PostgresRunQueue
) -> None:
    """FR-082. A reviewer who typed the right identifier is not told they did not."""
    run = _run(queue)
    queue.entomb([run], now=datetime.now(UTC), policy="retention")

    response = client.post(
        f"/v1/runs/{run.run_id}/corrections", json=_correction_body(), headers=_headers()
    )

    assert response.status_code == 410
    body = response.json()
    assert body["error"] == "run_erased"
    assert body["policy"] == "retention"


# -- GET /v1/runs/{run_id}/corrections ---------------------------------------


def test_the_listing_shows_this_tenants_corrections_and_no_others(
    client: TestClient, queue: PostgresRunQueue
) -> None:
    """FR-079 from the reading side."""
    run = _run(queue)
    client.post(f"/v1/runs/{run.run_id}/corrections", json=_correction_body(), headers=_headers())

    mine = client.get(f"/v1/runs/{run.run_id}/corrections", headers=_headers())
    theirs = client.get(f"/v1/runs/{run.run_id}/corrections", headers=_headers(STRANGER_KEY))

    assert len(mine.json()["corrections"]) == 1
    assert theirs.json()["corrections"] == []


def test_listing_a_malformed_identifier_is_a_404(client: TestClient) -> None:
    assert client.get("/v1/runs/not-a-uuid/corrections", headers=_headers()).status_code == 404


# -- POST /v1/callbacks ------------------------------------------------------


def test_registering_a_callback_never_returns_the_secret(client: TestClient) -> None:
    """FR-066. The table holds a digest; nothing can show the secret again."""
    response = client.post(
        "/v1/callbacks", json={"url": CALLBACK_URL, "secret": SECRET}, headers=_headers()
    )

    assert response.status_code == 201, response.text
    body = response.json()
    assert body["url"] == CALLBACK_URL
    assert body["callback_id"]
    assert SECRET not in response.text
    assert "secret" not in body


def test_a_callback_without_a_url_or_a_secret_is_a_422(client: TestClient) -> None:
    for body in ({"secret": SECRET}, {"url": CALLBACK_URL}, {}):
        response = client.post("/v1/callbacks", json=body, headers=_headers())
        assert response.status_code == 422, body


def test_a_refused_destination_does_not_report_what_it_resolved_to(
    client: TestClient,
) -> None:
    """A 422 naming the addresses would make this route a name resolver."""
    response = client.post(
        "/v1/callbacks",
        json={"url": "ftp://example.com/hook", "secret": SECRET},
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"] == "destination_refused"


def test_a_private_destination_is_refused_when_the_policy_is_not_relaxed(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-060, with the switch the other fixtures set turned back off.

    The deliverer is built once and cached, so this reaches into it rather than
    rebuilding the app — the object under test is the destination policy, not
    the wiring that chose it.
    """
    from docdoc.api.app import _Deployment

    deployment: _Deployment = client.app.state.deployment  # type: ignore[attr-defined]
    monkeypatch.setattr(deployment.deliverer(), "allow_private", False)

    response = client.post(
        "/v1/callbacks",
        json={"url": "http://127.0.0.1:9/hook", "secret": SECRET},
        headers=_headers(),
    )

    assert response.status_code == 422
    assert response.json()["error"] == "destination_refused"


# -- DELETE /v1/callbacks/{callback_id} --------------------------------------


def test_revoking_a_callback_is_204_and_idempotent(client: TestClient) -> None:
    """The shape credential revocation has, for the same reason."""
    identity = client.post(
        "/v1/callbacks", json={"url": CALLBACK_URL, "secret": SECRET}, headers=_headers()
    ).json()["callback_id"]

    first = client.delete(f"/v1/callbacks/{identity}", headers=_headers())
    second = client.delete(f"/v1/callbacks/{identity}", headers=_headers())

    assert first.status_code == second.status_code == 204


def test_revoking_a_malformed_identifier_is_also_204(client: TestClient) -> None:
    """Malformed and unknown are one answer, or the route names which identifiers
    are well-formed enough to exist."""
    assert client.delete("/v1/callbacks/not-a-uuid", headers=_headers()).status_code == 204


# -- GET /v1/runs/{run_id}/delivery ------------------------------------------


def test_a_run_with_a_callback_reports_its_delivery(
    client: TestClient, queue: PostgresRunQueue
) -> None:
    """FR-059. The delivery row is written by `finish`, in the same transaction."""
    from uuid import UUID

    identity = client.post(
        "/v1/callbacks", json={"url": CALLBACK_URL, "secret": SECRET}, headers=_headers()
    ).json()["callback_id"]
    run = _run(queue, callback_id=UUID(identity))

    response = client.get(f"/v1/runs/{run.run_id}/delivery", headers=_headers())

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["state"] == "pending"
    assert body["attempts"] == 0
    assert set(body) == {
        "delivery_id",
        "state",
        "attempts",
        "last_status",
        "last_error",
        "next_attempt_at",
    }


def test_a_run_nobody_asked_to_be_notified_about_is_a_404(
    client: TestClient, queue: PostgresRunQueue
) -> None:
    """An absent delivery is not an error; it is a resource never created."""
    run = _run(queue)

    assert client.get(f"/v1/runs/{run.run_id}/delivery", headers=_headers()).status_code == 404


def test_another_tenant_cannot_read_a_delivery(client: TestClient, queue: PostgresRunQueue) -> None:
    from uuid import UUID

    identity = client.post(
        "/v1/callbacks", json={"url": CALLBACK_URL, "secret": SECRET}, headers=_headers()
    ).json()["callback_id"]
    run = _run(queue, callback_id=UUID(identity))

    assert (
        client.get(f"/v1/runs/{run.run_id}/delivery", headers=_headers(STRANGER_KEY)).status_code
        == 404
    )


def test_a_malformed_run_identifier_has_no_delivery(client: TestClient) -> None:
    assert client.get("/v1/runs/not-a-uuid/delivery", headers=_headers()).status_code == 404


# -- with nothing to sign with (FR-064) --------------------------------------


def test_a_deployment_with_no_signing_secret_registers_no_callbacks(
    tmp_path, queue: PostgresRunQueue, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The honest wiring, not a degraded one.

    Every delivery is signed, so a deployment with no secret would accept a
    registration and then fail every attempt for a missing key — six outbound
    requests per run to report a configuration problem.
    """
    from docdoc.api.app import _Deployment, build_app

    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps({"keys": [{"sha256": digest_of(OWNER_KEY), "tenant_id": TENANT}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))
    monkeypatch.delenv(DELIVERY_SECRETS_FILE_ENV, raising=False)

    with TestClient(build_app(_Deployment(runs=queue))) as unsigned:
        registered = unsigned.post(
            "/v1/callbacks", json={"url": CALLBACK_URL, "secret": SECRET}, headers=_headers()
        )
        revoked = unsigned.delete(
            "/v1/callbacks/00000000-0000-4000-8000-000000000000", headers=_headers()
        )

    assert registered.status_code == 503
    # Still `204`: revoking something that cannot exist changed nothing, which
    # is what idempotent revocation means.
    assert revoked.status_code == 204
