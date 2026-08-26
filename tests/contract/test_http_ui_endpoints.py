"""Every claim in ``contracts/http-api-additions.md``, asserted rather than described.

Two endpoints and the assets, organised by the contract's own sections. Three of
these are worth reading before the rest.

**The storeless route writes nothing whatever the deployment is configured with**
(§2). That is a property of the endpoint, not of the configuration, and it is the
one most likely to be lost to a later change that reaches for
``deployment.artifact_store()`` because it happens to be there.

**A storeless run has no job**, because a storeless run writes no terminal
artifact and ADR-0003's ``processing_id`` *is* the terminal artifact id. The field
is absent rather than null, so a caller cannot pass it to ``GET /v1/jobs/{id}``
and receive ``unknown`` for an identity we invented.

**The route set is closed and no response carries a session** (FR-031, FR-060).
This milestone adds a browser interface and no authentication, and "no per-user
state" is the sort of promise that survives only if something fails when it stops
being true.
"""

from __future__ import annotations

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

#: Every route this interface has, after Milestone 8. The set is asserted rather
#: than described because "no per-user state" is a claim about what does *not*
#: exist, and only an exhaustive list can carry it (FR-031).
ROUTES = {
    ("POST", "/v1/documents"),
    ("GET", "/v1/documents/{blob_id}"),
    ("POST", "/v1/documents/{blob_id}/extract"),
    ("POST", "/v1/extract"),
    ("GET", "/v1/schemas"),
    ("GET", "/v1/jobs/{job_id}"),
    ("GET", "/v1/jobs/{job_id}/result"),
}


def _registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths([Path("schemas")])


def _adapter() -> EchoAdapter:
    return EchoAdapter.from_fixtures("tests/fixtures/echo")


@pytest.fixture
def storeless() -> TestClient:
    """A deployment with no store at all — the one this milestone unblocks."""
    return TestClient(build_app(_Deployment(registry=_registry(), adapter=_adapter())))


@pytest.fixture
def stored(tmp_path: Path) -> TestClient:
    return TestClient(
        build_app(
            _Deployment(
                store=FileArtifactStore(tmp_path),
                blobs=BlobStore(tmp_path),
                registry=_registry(),
                adapter=_adapter(),
            )
        )
    )


@pytest.fixture
def source() -> bytes:
    return FIXTURE.read_bytes()


def _extract(client: TestClient, source: bytes, schema: str = SCHEMA) -> Any:
    return client.post("/v1/extract", params={"schema": schema}, content=source)


# -- §2 the storeless run ----------------------------------------------------


def test_a_deployment_with_no_store_can_now_run_an_extraction(
    storeless: TestClient, source: bytes
) -> None:
    """FR-001, FR-003 — the request Milestone 7 could not serve at all.

    Before this endpoint, every route capable of extracting took a ``blob_id``,
    a ``blob_id`` existed only after a submission, and submission was refused
    without a store. The refusal was not a policy about extraction; it was a
    consequence of the only input shape on offer.
    """
    response = _extract(storeless, source)

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_identity"] == SCHEMA
    assert payload["grounding"] is not None
    assert payload["validation"] is not None


def test_a_storeless_run_offers_no_job_identity(storeless: TestClient, source: bytes) -> None:
    """FR-003 — absent, not null.

    ``job_id: null`` would say the same thing and invite a caller to send it to
    ``GET /v1/jobs/{id}``, which would answer ``unknown`` about an identity
    nobody issued. Omitting the field ends the conversation earlier.
    """
    payload = _extract(storeless, source).json()

    assert "job_id" not in payload


def test_the_storeless_route_writes_nothing_even_with_a_store_configured(
    stored: TestClient, source: bytes, tmp_path: Path
) -> None:
    """FR-008, SC-008 — the endpoint decides, never the configuration.

    The deployment here has a perfectly good store. The route still uses none,
    because whether a run persists is a property of the path the caller chose.
    """
    before = {path for path in tmp_path.rglob("*") if path.is_file()}

    assert _extract(stored, source).status_code == 200

    assert {path for path in tmp_path.rglob("*") if path.is_file()} == before


def test_storeless_and_store_backed_agree_on_everything_but_the_job(
    storeless: TestClient, stored: TestClient, source: bytes
) -> None:
    """FR-004, SC-006 — one pipeline, two doors.

    Compared field by field rather than by a spot check: the claim is that the
    interface serialises a result and does not produce a different one.
    """
    blob_id = stored.post("/v1/documents", content=source).json()["blob_id"]
    backed = stored.post(f"/v1/documents/{blob_id}/extract", params={"schema": SCHEMA}).json()
    free = _extract(storeless, source).json()

    backed.pop("job_id")
    # Durations and executed/reused statuses necessarily differ between two runs
    # — the same exception Milestone 7's SC-002 states and for the same reason.
    backed.pop("outcomes")
    free.pop("outcomes")

    assert free == backed


def test_the_media_type_comes_from_the_bytes_and_not_the_header(
    storeless: TestClient, source: bytes
) -> None:
    """FR-005 — a ``Content-Type`` is an assertion by the sender; this is a check."""
    response = storeless.post(
        "/v1/extract",
        params={"schema": SCHEMA},
        content=source,
        headers={"content-type": "text/plain"},
    )

    assert response.status_code == 200


def test_a_disallowed_type_is_refused_before_any_parser_runs(storeless: TestClient) -> None:
    """FR-005 — refused on the bytes, with a typed error."""
    response = storeless.post(
        "/v1/extract", params={"schema": SCHEMA}, content=b"just some text, not a document"
    )

    assert response.status_code == 415
    assert response.json()["error"]["class"] == "UnsupportedDocumentError"


def test_an_oversized_document_is_refused(storeless: TestClient, source: bytes) -> None:
    """FR-005 — the request cap fires while reading, before the body is buffered."""
    client = TestClient(
        build_app(
            _Deployment(registry=_registry(), adapter=_adapter(), max_request_bytes=len(source) - 1)
        )
    )

    assert client.post("/v1/extract", params={"schema": SCHEMA}, content=source).status_code == 413


def test_an_unknown_schema_is_a_typed_error_naming_what_exists(
    storeless: TestClient, source: bytes
) -> None:
    """FR-006 — no silent fallback to a neighbouring version (Principle VIII)."""
    response = _extract(storeless, source, schema="no-such-schema@1")

    assert response.status_code == 400
    assert response.json()["error"]["class"] == "SchemaError"


# -- §3 listing schemas ------------------------------------------------------


def test_every_listed_identity_is_accepted_verbatim_by_extract(
    storeless: TestClient, source: bytes
) -> None:
    """FR-010 — the listing is the set ``resolve()`` accepts, not a rendering of it.

    The strongest available statement of "runnable without translation": take
    what the endpoint offers and run it.
    """
    listing = storeless.get("/v1/schemas").json()["schemas"]
    assert listing

    for entry in listing:
        assert _extract(storeless, source, schema=entry["identity"]).status_code == 200


def test_the_listing_reveals_nothing_about_where_a_schema_lives(
    storeless: TestClient, tmp_path: Path
) -> None:
    """FR-011 — this endpoint is unauthenticated, like the rest of the interface."""
    body = storeless.get("/v1/schemas").text

    assert "/" not in body.replace("\\/", "")
    assert "schemas" not in body.removeprefix('{"schemas":')


def test_a_deployment_with_no_schemas_returns_an_empty_list_and_not_an_error() -> None:
    """FR-012 — configured with nothing is still configured."""
    client = TestClient(build_app(_Deployment(registry=SchemaRegistry(), adapter=_adapter())))

    response = client.get("/v1/schemas")

    assert response.status_code == 200
    assert response.json() == {"schemas": []}


# -- FR-031, FR-060 what must never accumulate -------------------------------


def test_the_route_set_is_exactly_these_seven(storeless: TestClient) -> None:
    """FR-031, FR-060 — an exhaustive list is the only way to assert an absence.

    A route added without a decision shows up here, which is the point: this
    milestone's argument that it is not the deferred "full review UI" rests on
    there being nowhere to send a correction to.
    """
    paths = storeless.app.openapi()["paths"]  # type: ignore[attr-defined]
    actual = {
        (method.upper(), path)
        for path, methods in paths.items()
        for method in methods
        if path.startswith("/v1")
    }

    assert actual == ROUTES


def test_no_response_carries_a_session(storeless: TestClient, source: bytes) -> None:
    """FR-031, FR-060 — no accounts, no sessions, no server-side record of a visit."""
    responses = [
        storeless.get("/v1/schemas"),
        _extract(storeless, source),
        storeless.get("/v1/jobs/sha256:" + "0" * 64),
    ]

    for response in responses:
        assert "set-cookie" not in {key.lower() for key in response.headers}


# -- §5 the corrected contract of Milestone 7 --------------------------------


def test_milestone_sevens_contract_no_longer_contradicts_the_code() -> None:
    """FR-007, SC-012 — the sentence is true now, rather than deleted.

    A contract that disagrees with the code is worse than one that is silent,
    because it is trusted. This asserts the disagreement is gone at its source:
    the claim that running an extraction needs no store is now true of a route
    that exists.
    """
    contract = Path("specs/007-pipeline-api-cli/contracts/http-api.md").read_text(encoding="utf-8")

    assert "POST /v1/extract" in contract or "/v1/extract" in contract
    assert "Running an extraction and reading what it produced do not" not in contract


# -- §4 the assets ------------------------------------------------------------


def test_a_missing_interface_says_what_is_missing_and_what_fixes_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FR-037 — never a blank page, never an obscure failure.

    A deployment without the ``ui`` extra is ordinary and supported, so this is
    not an error condition. It is a question with an answer, and the answer names
    a command.

    **The absence is forced rather than assumed.** As first written this read the
    ambient state, so it passed on a checkout where nobody had run the build and
    failed on one where somebody had — measuring the developer's shell rather than
    the behaviour. A test whose result depends on whether you happened to run
    ``npm run build`` is not testing this endpoint.
    """
    from docdoc.api import ui as ui_module

    monkeypatch.setattr(ui_module, "locate_assets", lambda: None)
    client = TestClient(build_app(_Deployment(registry=_registry(), adapter=_adapter())))

    response = client.get("/ui")

    assert response.status_code == 501
    body = response.json()["error"]
    assert body["class"] == "ViewerNotInstalled"
    assert body["message"]


def test_a_built_interface_is_served_from_this_origin(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FR-034 — same origin, so no cross-origin configuration exists anywhere.

    The other half of the case above, and forced the same way. Mounted under
    ``/ui`` rather than at the root so that adding an interface cannot shadow an
    API path — which this asserts by checking both still answer.
    """
    from docdoc.api import ui as ui_module

    (tmp_path / "index.html").write_text("<!doctype html><div id=root></div>", encoding="utf-8")
    monkeypatch.setattr(ui_module, "locate_assets", lambda: tmp_path)
    client = TestClient(build_app(_Deployment(registry=_registry(), adapter=_adapter())))

    assert client.get("/ui/").status_code == 200
    assert client.get("/v1/schemas").status_code == 200


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        ("configured", "DOCDOC_UI_ROOT"),
        ("installed-but-empty", "built wrongly"),
        ("checkout", "npm run build"),
        ("absent", "docdoc[ui]"),
    ],
)
def test_each_kind_of_absence_gets_its_own_sentence(
    state: str, expected: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """FR-037 — telling a developer to ``pip install`` what they can build is useless accuracy.

    Four states, four answers. The distinction is the requirement: "not found"
    would be true in every case and helpful in none.

    **Every state is forced.** Each of the four is also a state a machine can
    genuinely be in — a dev checkout has `ui/dist`, and a checkout with the `ui`
    extra synced has an *installed* `docdoc_ui` with no assets in it — so reading
    the ambient one would make this test report on the developer's machine
    instead of on `absence_reason`. It did exactly that once, passing before the
    `ui` extra existed and failing the moment it did.
    """
    from docdoc.api import ui as ui_module

    monkeypatch.delenv("DOCDOC_UI_ROOT", raising=False)
    monkeypatch.setattr(ui_module, "_installed_root", lambda: None)
    monkeypatch.setattr(ui_module, "_checkout_root", lambda: None)

    if state == "configured":
        monkeypatch.setenv("DOCDOC_UI_ROOT", str(tmp_path))
    if state == "installed-but-empty":
        monkeypatch.setattr(ui_module, "_installed_root", lambda: tmp_path)
    if state == "checkout":
        monkeypatch.setattr(ui_module, "_checkout_root", lambda: tmp_path)
    if state == "absent":
        monkeypatch.chdir(tmp_path)

    assert expected in ui_module.absence_reason()


def test_the_blob_shaped_route_still_requires_a_store(storeless: TestClient, source: bytes) -> None:
    """FR-007 — the corrected contract's other half.

    ``POST /v1/documents/{blob_id}/extract`` needs a store and cannot not need
    one: its input is a ``blob_id``. Nothing here relaxed that; a second route
    was added beside it.
    """
    response = storeless.post(
        "/v1/documents/sha256:" + "0" * 64 + "/extract", params={"schema": SCHEMA}
    )

    assert response.status_code >= 400
