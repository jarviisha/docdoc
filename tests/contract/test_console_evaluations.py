"""The operator checks answers and runs evaluations for any tenant (ADR-0023 §3)."""

from __future__ import annotations

import pytest
from tests.contract.test_evaluation_routes import (  # noqa: F401
    TYPED,
    _drain,
    blob,
    deployment,
)
from tests.support.console import ADMIN_KEY, H, Ring, accounts_with_alice, sign_in

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

pytestmark = pytest.mark.postgres


@pytest.fixture
def operator(deployment):  # noqa: F811
    from docdoc.api.app import _Deployment, build_app

    deployment = _Deployment(
        runs=deployment._runs,
        store_root=deployment._store_root,
        registry=deployment.registry(),
        console=accounts_with_alice(),
        keys=Ring(),
    )
    client = TestClient(build_app(deployment))
    return deployment, client, {**sign_in(client), **H}


def test_an_operator_check_is_recorded_as_the_operator(operator, blob) -> None:  # noqa: F811
    dep, client, headers = operator
    eid = client.post(
        "/v1/console/tenants/acme/evaluations", params={"schema": "invoice"}, headers=headers
    ).json()["evaluation_id"]
    _drain(dep)
    (document,) = client.get(f"/v1/console/tenants/acme/evaluations/{eid}", headers=headers).json()[
        "documents"
    ]
    run = type("R", (), {"run_id": document["run_id"]})

    posted = client.post(
        f"/v1/console/runs/{run.run_id}/corrections",
        json={
            "report_id": "sha256:" + "b" * 64,
            "document_id": blob,
            "field_path": "total",
            "annotator": "someone-else",
            "timestamp": "2026-09-09T00:00:00Z",
            "corrected_value": TYPED,
            "predicted_value": "1240.00",
            "reason": "checked",
        },
        headers=headers,
    )

    assert posted.status_code == 201
    listed = client.get(f"/v1/console/runs/{run.run_id}/corrections", headers=headers).json()
    assert [c["annotator"] for c in listed["corrections"]] == ["alice"]


def test_a_console_check_without_an_annotator_is_recorded_as_the_operator(
    operator,
    blob,  # noqa: F811
) -> None:
    dep, client, headers = operator
    eid = client.post(
        "/v1/console/tenants/acme/evaluations", params={"schema": "invoice"}, headers=headers
    ).json()["evaluation_id"]
    _drain(dep)
    (document,) = client.get(f"/v1/console/tenants/acme/evaluations/{eid}", headers=headers).json()[
        "documents"
    ]

    posted = client.post(
        f"/v1/console/runs/{document['run_id']}/corrections",
        json={
            "report_id": "sha256:" + "b" * 64,
            "document_id": blob,
            "field_path": "total",
            "timestamp": "2026-09-09T00:00:00Z",
            "corrected_value": TYPED,
            "predicted_value": "1240.00",
            "reason": "checked",
        },
        headers=headers,
    )

    assert posted.status_code == 201
    listed = client.get(
        f"/v1/console/runs/{document['run_id']}/corrections", headers=headers
    ).json()
    assert [c["annotator"] for c in listed["corrections"]] == ["alice"]


def test_console_evaluation_round_trip(operator, blob) -> None:  # noqa: F811
    dep, client, headers = operator
    base = "/v1/console/tenants/acme"

    assert client.get(f"{base}/answers", headers=headers).json() == {
        "schemas": [{"schema_name": "invoice", "documents": 1, "fields": 3}]
    }
    started = client.post(f"{base}/evaluations", params={"schema": "invoice"}, headers=headers)
    assert started.status_code == 202
    eid = started.json()["evaluation_id"]
    _drain(dep)

    done = client.get(f"{base}/evaluations/{eid}", headers=headers).json()
    assert done["status"] == "finished"
    listed = client.get(f"{base}/evaluations", headers=headers).json()["evaluations"]
    assert [e["evaluation_id"] for e in listed] == [eid]
    assert (
        client.get(f"/v1/console/tenants/globex/evaluations/{eid}", headers=headers).status_code
        == 404
    )


def test_console_evaluation_routes_need_a_session(operator, blob) -> None:  # noqa: F811
    _, client, _ = operator
    bearer = {**H, "Authorization": f"Bearer {ADMIN_KEY}"}
    anonymous = TestClient(client.app)

    for method, path in (
        ("GET", "/v1/console/tenants/acme/answers"),
        ("POST", "/v1/console/tenants/acme/evaluations?schema=invoice"),
        ("GET", "/v1/console/tenants/acme/evaluations"),
        ("GET", "/v1/console/runs/00000000-0000-0000-0000-000000000000/corrections"),
    ):
        r = anonymous.request(method, path, headers=bearer)
        assert r.status_code == 401
        assert r.json()["error"]["class"] == "ConsoleSessionRequired"
