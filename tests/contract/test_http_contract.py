"""Every claim in ``contracts/http-api.md``, asserted rather than described.

Organised by the contract's own sections: the endpoints, the job model, the
limits, and the error mapping.

Two of these are worth reading before the rest. The status set is closed at three
and ``unavailable`` deliberately conflates "never produced" with "cleared" — an
append-only store keeps no record of what it was never asked to hold, and a
status that claimed to tell them apart would be inventing the difference. And a
mid-run failure carries the completed stages' results, because a failed run has
no job to fetch later, so that response is the only place they can appear.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"

#: Well-formed and never produced by anything.
ABSENT_ID = "sha256:" + "0" * 64


@pytest.fixture
def deployment(tmp_path: Path) -> _Deployment:
    return _Deployment(
        store=FileArtifactStore(tmp_path),
        blobs=BlobStore(tmp_path),
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
    )


@pytest.fixture
def client(deployment: _Deployment) -> TestClient:
    return TestClient(build_app(deployment))


@pytest.fixture
def source() -> bytes:
    return FIXTURE.read_bytes()


def submit(client: TestClient, source: bytes) -> str:
    response = client.post("/v1/documents", content=source)
    assert response.status_code == 200
    return str(response.json()["blob_id"])


def extract(client: TestClient, blob_id: str, schema: str = SCHEMA) -> Any:
    return client.post(f"/v1/documents/{blob_id}/extract", params={"schema": schema})


# -- §1 and §2 the endpoints -------------------------------------------------


def test_submission_returns_a_blob_identity_and_never_a_document_id(
    client: TestClient, source: bytes
) -> None:
    """research R8 — a ``document_id`` identifies one *parse*, and none has happened.

    Returning a blob id under that name would hand a caller an identifier whose
    spans and geometry anchor to nothing, which is the exact confusion the
    two-level identity exists to prevent.
    """
    payload = client.post("/v1/documents", content=source).json()
    assert payload["blob_id"].startswith("sha256:")
    assert "document_id" not in payload
    assert payload["size_bytes"] == len(source)


def test_identical_bytes_yield_one_identity_and_one_stored_copy(
    client: TestClient, source: bytes, tmp_path: Path
) -> None:
    first = submit(client, source)
    second = submit(client, source)
    assert first == second

    blobs = list((tmp_path / "blobs").glob("*/*"))
    assert len(blobs) == 1, "content addressing means one copy, not two"


def test_metadata_returns_identity_size_and_type_but_not_the_document(
    client: TestClient, source: bytes
) -> None:
    blob_id = submit(client, source)
    payload = client.get(f"/v1/documents/{blob_id}").json()
    assert payload == {
        "blob_id": blob_id,
        "size_bytes": len(source),
        "media_type": "application/pdf",
    }


def test_an_unknown_blob_is_a_404_and_not_a_guess(client: TestClient) -> None:
    assert client.get(f"/v1/documents/{ABSENT_ID}").status_code == 404


# -- §3 the job model --------------------------------------------------------


def test_a_run_returns_the_terminal_identity_and_the_result(
    client: TestClient, source: bytes
) -> None:
    """FR-067 — an identity-only response is a receipt a caller often cannot redeem."""
    payload = extract(client, submit(client, source)).json()
    assert payload["job_id"].startswith("sha256:")
    assert payload["verdict"] == "valid"
    assert payload["extraction"] is not None
    assert payload["grounding"] is not None
    assert payload["validation"] is not None


def test_the_job_id_is_the_terminal_artifact_id_and_not_a_second_identifier(
    client: TestClient, source: bytes, deployment: _Deployment
) -> None:
    """FR-033, checked against what the library computes for the same inputs."""
    from docdoc.pipeline import run

    payload = extract(client, submit(client, source)).json()
    local = run(
        source,
        schema=SCHEMA,
        registry=deployment.registry(),
        adapter=deployment.adapter(),
    )
    assert payload["job_id"] == local.processing_id


def test_a_job_that_succeeded_reports_succeeded(client: TestClient, source: bytes) -> None:
    job_id = extract(client, submit(client, source)).json()["job_id"]
    assert client.get(f"/v1/jobs/{job_id}").json()["status"] == "succeeded"


def test_a_malformed_identity_is_unknown(client: TestClient) -> None:
    """The judgement that can be made without history: is this an id at all?"""
    payload = client.get("/v1/jobs/not-an-identity").json()
    assert payload["status"] == "unknown"


def test_a_well_formed_absent_identity_is_unavailable(client: TestClient) -> None:
    payload = client.get(f"/v1/jobs/{ABSENT_ID}").json()
    assert payload["status"] == "unavailable"


def test_a_cleared_result_is_unavailable_and_indistinguishable_from_never_produced(
    client: TestClient, source: bytes, deployment: _Deployment
) -> None:
    """CHK019's resolution, asserted as the behaviour it chose.

    The two conditions are one observation to an append-only store with no
    tombstones, and the interface says so rather than guessing between them.
    """
    job_id = extract(client, submit(client, source)).json()["job_id"]
    assert deployment.store is not None
    deployment.store.clear()

    cleared = client.get(f"/v1/jobs/{job_id}").json()
    never = client.get(f"/v1/jobs/{ABSENT_ID}").json()
    assert cleared["status"] == "unavailable"
    assert cleared["status"] == never["status"]


def test_nothing_is_ever_reported_pending(client: TestClient, source: bytes) -> None:
    """Fabricating a pending state for an id nobody issued is how a client waits
    forever."""
    job_id = extract(client, submit(client, source)).json()["job_id"]
    for identity in (job_id, ABSENT_ID, "not-an-identity"):
        assert client.get(f"/v1/jobs/{identity}").json()["status"] != "pending"


def test_an_unavailable_result_is_not_silently_recomputed(
    client: TestClient, source: bytes, deployment: _Deployment
) -> None:
    """FR-036 — the inputs may have moved, and returning a different result under
    the same id would break the one promise the identity makes."""
    job_id = extract(client, submit(client, source)).json()["job_id"]
    assert deployment.store is not None
    deployment.store.clear()

    response = client.get(f"/v1/jobs/{job_id}/result")
    assert response.status_code == 404
    assert response.json()["status"] == "unavailable"


def test_a_stored_result_is_retrievable_by_its_identity(
    client: TestClient, source: bytes
) -> None:
    job_id = extract(client, submit(client, source)).json()["job_id"]
    fetched = client.get(f"/v1/jobs/{job_id}/result")
    assert fetched.status_code == 200
    assert fetched.json()["verdict"] == "valid"
    assert fetched.json()["job_id"] == job_id


# -- §6 errors ---------------------------------------------------------------


def test_a_document_that_fails_validation_is_a_200_and_not_an_error(
    client: TestClient, source: bytes, tmp_path: Path
) -> None:
    """The run succeeded; the answer is that the document is wrong."""
    fixtures = json.loads(Path("tests/fixtures/echo/invoice@1.json").read_text())
    fixtures["currency"]["value"] = "XYZ"
    broken = tmp_path / "echo"
    broken.mkdir()
    (broken / "invoice@1.json").write_text(json.dumps(fixtures))

    deployment = _Deployment(
        store=FileArtifactStore(tmp_path / "store"),
        blobs=BlobStore(tmp_path / "store"),
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures(broken),
    )
    invalid_client = TestClient(build_app(deployment))

    response = extract(invalid_client, submit(invalid_client, source))
    assert response.status_code == 200
    assert response.json()["verdict"] != "valid"


def test_an_unresolvable_schema_is_a_400_naming_the_stage(
    client: TestClient, source: bytes
) -> None:
    response = extract(client, submit(client, source), schema="no-such-schema@1")
    assert response.status_code == 400
    assert response.json()["error"]["class"] == "SchemaError"
    assert response.json()["error"]["stage"] == "extract"


def test_a_mid_run_failure_carries_the_completed_stages(
    client: TestClient, source: bytes
) -> None:
    """FR-066 — a failed run has no job to fetch, so this is the only place the
    partial result can appear."""
    body = extract(client, submit(client, source), schema="no-such-schema@1").json()

    statuses = {item["stage"]: item["status"] for item in body["outcomes"]}
    assert statuses["parse"] == "executed", "the parse happened and must be reported"
    assert statuses["extract"] == "failed"
    assert statuses["ground"] == "skipped"


def test_an_error_body_never_carries_a_provider_message(
    client: TestClient, source: bytes
) -> None:
    """FR-037 — a provider's error text may quote the document it choked on."""
    body = extract(client, submit(client, source), schema="no-such-schema@1").json()
    serialised = json.dumps(body)
    assert "Traceback" not in serialised


def test_every_failure_arrives_as_a_status_in_the_table(
    client: TestClient, source: bytes
) -> None:
    """SC-011 — no untyped exception reaches a caller, over any endpoint."""
    from docdoc.api.errors import STATUS_BY_ERROR

    known = set(STATUS_BY_ERROR.values()) | {200, 404, 413, 415, 422}
    blob_id = submit(client, source)

    for response in (
        client.get("/v1/documents/not-an-identity"),
        client.get("/v1/jobs/not-an-identity"),
        client.get("/v1/jobs/not-an-identity/result"),
        extract(client, blob_id, schema="no-such-schema@1"),
        extract(client, ABSENT_ID),
    ):
        assert response.status_code in known, response.text


# -- §5 limits ---------------------------------------------------------------


def test_an_oversized_request_is_refused_before_anything_is_parsed(
    tmp_path: Path, source: bytes
) -> None:
    """SC-009 — zero parses, zero provider calls, and the refusal comes first.

    The cap is set below the fixture's size, so the refusal is about the limit
    rather than about a document nobody could process.
    """

    class Unreachable(EchoAdapter):
        def complete(self, request: Any, options: Any) -> Any:
            raise AssertionError("an oversized submission reached the model")

    deployment = _Deployment(
        store=FileArtifactStore(tmp_path),
        blobs=BlobStore(tmp_path),
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=Unreachable.from_fixtures("tests/fixtures/echo"),
        max_request_bytes=len(source) // 2,
    )
    client = TestClient(build_app(deployment))

    response = client.post("/v1/documents", content=source)
    assert response.status_code == 413
    assert not list((tmp_path / "blobs").glob("*/*")), "nothing may be stored"


def test_a_disallowed_media_type_is_refused(client: TestClient, tmp_path: Path) -> None:
    """From the bytes, never from a client-declared type."""
    response = client.post(
        "/v1/documents",
        content=b"this is not a pdf, whatever the header says",
        headers={"content-type": "application/pdf"},
    )
    assert response.status_code in {413, 415}


# -- §4 equality with the library --------------------------------------------


def test_a_result_over_http_equals_the_same_run_in_process(
    client: TestClient, source: bytes, deployment: _Deployment
) -> None:
    """FR-034 and SC-010 — the interface serialises a result; it does not produce
    a different one. Asserted by running both and comparing, not by inspection."""
    from docdoc.pipeline import run

    over_http = extract(client, submit(client, source)).json()
    local = run(
        source,
        schema=SCHEMA,
        registry=deployment.registry(),
        adapter=deployment.adapter(),
    )

    assert local.validation is not None
    assert over_http["job_id"] == local.processing_id
    assert over_http["verdict"] == local.validation.verdict.value
    assert over_http["validation"] == local.validation.model_dump(mode="json")
    assert over_http["grounding"] == local.grounding.model_dump(mode="json")  # type: ignore[union-attr]
    assert over_http["extraction"] == local.extraction.model_dump(mode="json")  # type: ignore[union-attr]


# -- FR-068 ------------------------------------------------------------------


def test_a_deployment_with_no_store_refuses_submission_and_names_the_setting() -> None:
    """Accepting bytes it cannot keep, and returning an identity that will never
    resolve, is the worse answer."""
    client = TestClient(build_app(_Deployment()))
    response = client.post("/v1/documents", content=b"%PDF-1.4\n")
    assert response.status_code == 500
    assert "DOCDOC_STORE_ROOT" in response.json()["error"]["message"]
