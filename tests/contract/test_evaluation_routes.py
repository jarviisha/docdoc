"""Tenants read their answers and run evaluations over HTTP."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest
from tests.infra import require_database

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.evaluation.corrections import Correction
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.runs import migrations
from docdoc.runs.corrections import ANSWERS_NEVER_EXPIRE
from docdoc.runs.identity import DEFAULT_LEASE
from docdoc.runs.postgres import PostgresRunQueue
from docdoc.runs.principal import digest_of
from docdoc.runs.worker import execute_one

pytestmark = pytest.mark.postgres

TENANT, OTHER = "acme", "globex"
PID = "sha256:" + "b" * 64
PDF = Path("tests/fixtures/pdf/digital_invoice.pdf")
TYPED = "1,240.00 as typed"


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    psycopg = pytest.importorskip("psycopg")
    from docdoc.api.app import _Deployment

    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs, corrections, run_tombstones, evaluations")
    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps(
            {
                "keys": [
                    {"sha256": digest_of("a-key"), "tenant_id": TENANT},
                    {"sha256": digest_of("b-key"), "tenant_id": OTHER},
                ]
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))
    monkeypatch.delenv("DOCDOC_LIMITS_FILE", raising=False)
    return _Deployment(
        runs=PostgresRunQueue(lambda: psycopg.connect(dsn)),
        store_root=tmp_path / "store",
        registry=SchemaRegistry.from_paths([Path("schemas")]),
    )


@pytest.fixture
def client(deployment) -> TestClient:
    from docdoc.api.app import build_app

    return TestClient(build_app(deployment))


A = {"Authorization": "Bearer a-key"}
B = {"Authorization": "Bearer b-key"}


def _check(deployment, blob_id: str, field: str, **fields) -> None:
    deployment.corrections().record(
        Correction(
            report_id=PID,
            document_id=blob_id,
            field_path=field,
            annotator="ana",
            timestamp=datetime(2026, 9, 9, tzinfo=UTC),
            reason="checked",
            **fields,
        ),
        tenant_id=TENANT,
        run_id=uuid4(),
        blob_id=blob_id,
        schema_name="invoice",
        at=datetime.now(UTC),
        expires_at=ANSWERS_NEVER_EXPIRE,
    )


@pytest.fixture
def blob(deployment) -> str:
    _, blobs = deployment.stores_for(TENANT)
    blob_id = blobs.put(PDF.read_bytes())
    _check(deployment, blob_id, "total", corrected_value=TYPED, predicted_value="1240.00")
    _check(deployment, blob_id, "currency", confirmed=True, predicted_value="USD")
    _check(deployment, blob_id, "due_date", confirmed=True, predicted_value=None)
    return blob_id


def _drain(deployment) -> None:
    queue = deployment.runs()
    store, blobs = deployment.stores_for(TENANT)
    now = datetime.now(UTC)
    while run := queue.claim(worker_id="w", now=now, lease=DEFAULT_LEASE, max_attempts=3):
        execute_one(
            run,
            queue=queue,
            blobs=blobs,
            store=store,
            registry=deployment.registry(),
            adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
            now=now,
        )


def test_the_answer_summary_counts(client, blob) -> None:
    body = client.get("/v1/answers", headers=A).json()

    assert body == {"schemas": [{"schema_name": "invoice", "documents": 1, "fields": 3}]}
    assert client.get("/v1/answers", headers=B).json() == {"schemas": []}


def test_starting_and_reading_an_evaluation(client, deployment, blob) -> None:
    started = client.post("/v1/evaluations", params={"schema": "invoice"}, headers=A)

    assert started.status_code == 202
    body = started.json()
    assert body["documents"] == 1
    assert body["status"] == "running"
    assert body["schema_identity"] == "invoice@2"
    path = f"/v1/evaluations/{body['evaluation_id']}"
    assert client.get(path, headers=A).json()["status"] == "running"

    _drain(deployment)
    done = client.get(path, headers=A).json()

    assert done["status"] == "finished"
    assert "field_accuracy" in done["metrics"]
    assert done["golden_set_id"]
    listed = client.get("/v1/evaluations", params={"schema": "invoice"}, headers=A).json()
    assert [e["evaluation_id"] for e in listed["evaluations"]] == [body["evaluation_id"]]
    assert "per_field" not in listed["evaluations"][0]


def test_a_finished_evaluation_shows_in_the_list_and_allows_a_new_start(
    client, deployment, blob
) -> None:
    client.post("/v1/evaluations", params={"schema": "invoice"}, headers=A)
    _drain(deployment)

    (listed,) = client.get("/v1/evaluations", headers=A).json()["evaluations"]

    assert listed["status"] == "finished"
    assert "field_accuracy" in listed["metrics"]
    assert (
        client.post("/v1/evaluations", params={"schema": "invoice"}, headers=A).status_code == 202
    )


def test_another_tenants_evaluation_is_not_found(client, deployment, blob) -> None:
    eid = client.post("/v1/evaluations", params={"schema": "invoice"}, headers=A).json()[
        "evaluation_id"
    ]

    assert client.get(f"/v1/evaluations/{eid}", headers=B).status_code == 404
    assert client.get(f"/v1/evaluations/{uuid4()}", headers=A).status_code == 404
    assert client.get("/v1/evaluations/not-a-uuid", headers=A).status_code == 404
    assert client.get("/v1/evaluations", headers=B).json() == {"evaluations": []}


def test_starting_with_no_answers_is_409(client) -> None:
    response = client.post("/v1/evaluations", params={"schema": "invoice"}, headers=A)

    assert response.status_code == 409
    assert response.json()["error"] == "no_answers"


def test_a_second_start_is_409_and_an_unknown_schema_is_404(client, blob) -> None:
    client.post("/v1/evaluations", params={"schema": "invoice"}, headers=A)

    again = client.post("/v1/evaluations", params={"schema": "invoice"}, headers=A)
    unknown = client.post("/v1/evaluations", params={"schema": "nope"}, headers=A)

    assert (again.status_code, again.json()["error"]) == (409, "evaluation_in_progress")
    assert unknown.status_code == 404


def test_the_drill_down_shows_values_not_hashes(client, deployment, blob) -> None:
    eid = client.post("/v1/evaluations", params={"schema": "invoice"}, headers=A).json()[
        "evaluation_id"
    ]
    _drain(deployment)

    body = client.get(f"/v1/evaluations/{eid}", headers=A).json()

    (document,) = body["documents"]
    assert document["document_id"] == blob
    outcomes = {o["field_path"]: o for o in document["outcomes"]}
    assert outcomes["total"]["expected"] == TYPED
    assert outcomes["total"]["extracted"] == "1240.00"
    assert outcomes["total"]["kind"] == "incorrect"
    assert outcomes["currency"]["expected"] == "USD"
    assert outcomes["currency"]["kind"] == "correct"
    assert outcomes["due_date"]["expected"] is None
    assert outcomes["due_date"]["extracted"] is None
    assert "total" in body["per_field"]


def _scored(client, deployment) -> dict:
    """Start an evaluation, run it, and read it back."""
    eid = client.post("/v1/evaluations", params={"schema": "invoice"}, headers=A).json()[
        "evaluation_id"
    ]
    _drain(deployment)
    return client.get(f"/v1/evaluations/{eid}", headers=A).json()


def _quantity_check(blob: str, value) -> dict:
    return {
        "report_id": PID,
        "document_id": blob,
        "field_path": "line_items[0].quantity",
        "annotator": "ana",
        "timestamp": "2026-09-09T00:00:00Z",
        "predicted_value": 5,
        "corrected_value": value,
        "reason": "counted",
    }


def test_a_numeric_correction_matches_a_numeric_extraction(client, deployment, blob) -> None:
    (document,) = _scored(client, deployment)["documents"]

    # The console posts what was typed: a string, for a `number` field.
    posted = client.post(
        f"/v1/runs/{document['run_id']}/corrections", json=_quantity_check(blob, "2"), headers=A
    )
    assert posted.status_code == 201

    # The echo extraction reads 2 for this field.
    (rescored,) = _scored(client, deployment)["documents"]
    outcomes = {o["field_path"]: o for o in rescored["outcomes"]}
    assert outcomes["line_items[0].quantity"]["kind"] == "correct"


def test_an_unparseable_value_is_refused(client, deployment, blob) -> None:
    (document,) = _scored(client, deployment)["documents"]

    posted = client.post(
        f"/v1/runs/{document['run_id']}/corrections", json=_quantity_check(blob, "two"), headers=A
    )

    assert posted.status_code == 422
    assert posted.json() == {"error": "invalid_value", "detail": "line_items[0].quantity"}
    assert "two" not in posted.text


def test_a_confirmed_check_without_grounding_stores_no_location(client, deployment, blob) -> None:
    from uuid import UUID

    from docdoc.api.runs import _terminal

    (document,) = _scored(client, deployment)["documents"]
    path = f"/v1/runs/{document['run_id']}/corrections"
    tick = {
        "report_id": PID,
        "document_id": blob,
        "annotator": "ana",
        "timestamp": "2026-09-09T00:00:00Z",
        "reason": "",
        "confirmed": True,
    }

    # `total` is ungrounded in the echo fixture.
    ungrounded = client.post(path, json={**tick, "field_path": "total"}, headers=A)
    # And with the run's grounding artifact gone, nothing is grounded.
    run = deployment.runs().get(UUID(document["run_id"]), TENANT)
    store, _ = deployment.stores_for(TENANT)
    store.delete(_terminal(store, run.processing_id).provenance.grounding_artifact_id)
    unreadable = client.post(path, json={**tick, "field_path": "currency"}, headers=A)

    assert (ungrounded.status_code, unreadable.status_code) == (201, 201)
    listed = client.get(path, headers=A).json()["corrections"]
    assert [(c["field_path"], c["location"]) for c in listed] == [
        ("total", None),
        ("currency", None),
    ]


def test_a_rate_limited_start_is_429(deployment, blob, monkeypatch) -> None:
    from docdoc.api.app import _Deployment, build_app

    # The limiter is read when a deployment is built, so build another.
    monkeypatch.setenv("DOCDOC_LIMIT_SUBMISSIONS_PER_MINUTE", "0")
    client = TestClient(
        build_app(
            _Deployment(
                runs=deployment._runs,
                store_root=deployment._store_root,
                registry=deployment.registry(),
            )
        )
    )

    response = client.post("/v1/evaluations", params={"schema": "invoice"}, headers=A)

    assert response.status_code == 429
    assert response.json()["error"] == "limit_exceeded"
    assert "retry-after" in response.headers


def test_no_database_answers_404(deployment) -> None:
    from docdoc.api.app import _Deployment, build_app

    client = TestClient(build_app(_Deployment()))

    assert client.get("/v1/answers", headers=A).status_code == 404
    assert client.post("/v1/evaluations", params={"schema": "x"}, headers=A).status_code == 404
    assert client.get("/v1/evaluations", headers=A).status_code == 404
    assert client.get(f"/v1/evaluations/{uuid4()}", headers=A).status_code == 404
