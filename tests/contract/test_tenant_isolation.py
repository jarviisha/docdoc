"""SC-008 — cross-tenant access is byte-identical to non-existence.

Not merely the same status: **the same body**. A status code tells an attacker
nothing; a message that said "not yours" for one identifier and "no such run" for
another is an existence oracle spelled in prose, and it is the kind that survives
review because each message is individually reasonable.

The three identifiers are covered because they fail differently. A `run_id` is
opaque and lives in a database; a `blob_id` and a `processing_id` are
content-addressed and live in a store, which means **the other tenant can derive
them without ever having seen them** — submit the same invoice and you have the
identity. That is what makes this test necessary rather than paranoid: for two of
the three, guessing is not the attack, arithmetic is.

The other half of isolation — that the *cost and timing* are identical too — is
`test_no_existence_oracle.py`, because no assertion about a response can reach it.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from tests.support import KEYS_FILE, bearer

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.api.auth import KeyRing
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"

#: Well-formed, content-addressed, and produced by nothing. The control against
#: which every cross-tenant response is compared.
ABSENT_ID = "sha256:" + "0" * 64

#: A syntactically valid UUID that names no run.
ABSENT_RUN = "00000000-0000-4000-8000-000000000000"


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """One deployment, two tenants, one store root.

    A shared root deliberately: the isolation being asserted is namespacing
    inside one store, and two roots would make the test pass by having no shared
    surface at all — the arrangement nobody deploys.
    """
    deployment = _Deployment(
        store_root=tmp_path,
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        keys=KeyRing.from_file(Path(KEYS_FILE)),
    )
    return TestClient(build_app(deployment))


def _submit(client: TestClient, tenant: str) -> str:
    response = client.post("/v1/documents", content=FIXTURE.read_bytes(), headers=bearer(tenant))
    assert response.status_code == 200, response.text
    return str(response.json()["blob_id"])


def _extract(client: TestClient, tenant: str, blob_id: str) -> Any:
    return client.post(
        f"/v1/documents/{blob_id}/extract",
        params={"schema": SCHEMA},
        headers=bearer(tenant),
    )


def _identical(
    mine: Any,
    absent: Any,
    what: str,
    *,
    asked: tuple[str, str] = ("", ""),
) -> None:
    """The assertion, in the strong form SC-008 asks for.

    ``asked`` names the identifier each request carried, and those are blanked
    before the bodies are compared. **An echo of the caller's own input is not a
    disclosure** — a caller who sent an identifier already knows it, and a route
    that repeats it back has told them nothing they did not type. Comparing
    bodies without this normalisation asserts that two *different requests* give
    the same answer, which is not a property anything should have.
    """
    assert mine.status_code == absent.status_code, (
        f"{what}: another tenant's identifier answered {mine.status_code} and a "
        f"non-existent one answered {absent.status_code}; the status alone tells "
        "a caller the identifier exists somewhere"
    )
    left = mine.text.replace(asked[0], "<asked>") if asked[0] else mine.text
    right = absent.text.replace(asked[1], "<asked>") if asked[1] else absent.text
    assert left == right, (
        f"{what}: the bodies differ, so the response distinguishes 'not yours' "
        f"from 'no such thing'. That is an existence oracle spelled in prose.\n"
        f"  cross-tenant: {left}\n  non-existent: {right}"
    )


# -- the credential itself ----------------------------------------------------


def test_no_credential_is_refused_before_anything_is_read(client: TestClient) -> None:
    """FR-059, FR-067. The refusal is a router dependency, so no body is read."""
    response = client.post("/v1/documents", content=FIXTURE.read_bytes())

    assert response.status_code == 401
    assert response.headers.get("www-authenticate") == "Bearer"


def test_an_unrecognised_key_is_refused_identically_to_an_absent_one(
    client: TestClient,
) -> None:
    """One error for absent, malformed, and unknown.

    A different message for each would tell an attacker which guesses are
    well-formed enough to be worth refining.
    """
    absent = client.get("/v1/schemas")
    unknown = client.get("/v1/schemas", headers={"Authorization": "Bearer nope"})
    malformed = client.get("/v1/schemas", headers={"Authorization": "Basic nope"})

    assert absent.status_code == unknown.status_code == malformed.status_code == 401
    assert absent.text == unknown.text == malformed.text


def test_a_valid_key_reaches_the_route(client: TestClient) -> None:
    """Guards every assertion above: a 401 for everything would satisfy them all."""
    assert client.get("/v1/schemas", headers=bearer("acme")).status_code == 200


# -- blob_id ------------------------------------------------------------------


def test_another_tenants_blob_is_indistinguishable_from_one_that_never_existed(
    client: TestClient,
) -> None:
    """FR-064. And note *how* globex knows the identifier: it hashed the file."""
    blob_id = _submit(client, "acme")

    theirs = client.get(f"/v1/documents/{blob_id}", headers=bearer("globex"))
    nothing = client.get(f"/v1/documents/{ABSENT_ID}", headers=bearer("globex"))

    _identical(theirs, nothing, "blob metadata", asked=(blob_id, ABSENT_ID))
    assert theirs.status_code == 404
    # And the owner can still read it, or this test would pass on a store that
    # lost the document.
    assert client.get(f"/v1/documents/{blob_id}", headers=bearer("acme")).status_code == 200


def test_another_tenants_blob_cannot_be_extracted_from(client: TestClient) -> None:
    """The dangerous shape: reading *through* an identifier rather than reading it.

    A 404 on the metadata route with a working extract route would be isolation
    that covers the door and not the window.
    """
    pytest.importorskip("pymupdf")
    blob_id = _submit(client, "acme")

    theirs = _extract(client, "globex", blob_id)
    nothing = _extract(client, "globex", ABSENT_ID)

    _identical(theirs, nothing, "extract over another tenant's blob", asked=(blob_id, ABSENT_ID))


# -- run_id -------------------------------------------------------------------


def test_another_tenants_run_is_indistinguishable_from_one_that_never_existed(
    client: TestClient,
) -> None:
    """FR-063, FR-066, over a deployment with no run database configured.

    Both answers come from the same branch — there is no queue to ask — which is
    exactly the property being asserted: one function produces the answer for
    unknown, malformed, and another tenant's alike, so the three cannot drift
    into three messages.
    """
    theirs = client.get(f"/v1/runs/{ABSENT_RUN}", headers=bearer("globex"))
    malformed = client.get("/v1/runs/not-a-uuid", headers=bearer("globex"))

    _identical(theirs, malformed, "run state", asked=(ABSENT_RUN, "not-a-uuid"))
    assert theirs.status_code == 404


# -- processing_id ------------------------------------------------------------


def test_another_tenants_result_is_indistinguishable_from_one_never_produced(
    client: TestClient,
) -> None:
    """FR-065, and the one where derivation makes guessing unnecessary.

    Both tenants process the same invoice against the same schema, so both
    derive the *same* `processing_id` — that is ADR-0003 working, not a bug. What
    must not follow is that either can read the other's copy.
    """
    pytest.importorskip("pymupdf")
    blob_id = _submit(client, "acme")
    produced = _extract(client, "acme", blob_id)
    assert produced.status_code == 200, produced.text
    processing_id = produced.json()["job_id"]

    theirs = client.get(f"/v1/jobs/{processing_id}", headers=bearer("globex"))
    nothing = client.get(f"/v1/jobs/{ABSENT_ID}", headers=bearer("globex"))
    theirs_result = client.get(f"/v1/jobs/{processing_id}/result", headers=bearer("globex"))
    nothing_result = client.get(f"/v1/jobs/{ABSENT_ID}/result", headers=bearer("globex"))

    _identical(theirs, nothing, "job status", asked=(processing_id, ABSENT_ID))
    _identical(theirs_result, nothing_result, "job result", asked=(processing_id, ABSENT_ID))

    # The owner reads it, so the isolation is not "nobody can read anything".
    assert client.get(f"/v1/jobs/{processing_id}", headers=bearer("acme")).status_code == 200


def test_two_tenants_derive_the_same_identity_and_that_is_correct(
    client: TestClient,
) -> None:
    """ADR-0014 §2: namespacing changes location and never identity (FR-085).

    Worth asserting positively rather than leaving implicit, because the obvious
    way to close the oracle — mixing the tenant into the derivation — would pass
    every isolation test above and break the property redelivery depends on: that
    a re-executed run recomputes the same terminal identity.
    """
    pytest.importorskip("pymupdf")
    acme = _extract(client, "acme", _submit(client, "acme"))
    globex = _extract(client, "globex", _submit(client, "globex"))

    assert acme.status_code == globex.status_code == 200
    assert acme.json()["job_id"] == globex.json()["job_id"], (
        "the two tenants derived different identities for identical bytes and an "
        "identical schema, so the tenant reached an identity derivation. That "
        "breaks ADR-0003 and, with it, redelivery"
    )


# -- every mounted path, not every route (T100) -------------------------------
#
# The tests above name the paths they check, which is how `/ui` stayed open:
# it is a `mount` rather than a route, so it inherited nothing from the router's
# dependency and appeared in nobody's list. FR-059 exempts liveness and readiness
# and nothing else, so the assertion has to be exhaustive rather than enumerated.


def _every_path(app: object) -> list[str]:
    """Every path the application will answer, routes **and** mounts.

    Walked off `app.routes` rather than off the OpenAPI document, deliberately:
    a mount does not appear in OpenAPI at all, which is precisely why one could
    sit outside authentication without anybody noticing.
    """
    found: list[str] = []
    for route in app.routes:  # type: ignore[attr-defined]
        path = getattr(route, "path", None)
        if not path or "{" in path:
            continue
        found.append(path)
    return found


def test_the_route_walk_finds_the_ui_mount(client: TestClient) -> None:
    """Guards the guard. A walk that missed mounts would have missed the defect."""
    paths = _every_path(client.app)

    assert "/ui" in paths, (
        "the mount is invisible to this walk, so the check below cannot see the "
        "one path that was actually unauthenticated"
    )
    assert {"/healthz", "/readyz"} <= set(paths)


def test_only_liveness_and_readiness_answer_without_a_credential(
    client: TestClient,
) -> None:
    """FR-059, stated as the closed exemption list the requirement actually is.

    Exhaustive rather than enumerated: a route or a mount added later is covered
    without anybody remembering to add it here, which is the failure this exists
    to prevent rather than the one it found.
    """
    unauthenticated: list[str] = []
    for path in _every_path(client.app):
        if client.get(path).status_code != 401:
            unauthenticated.append(path)

    assert set(unauthenticated) == {"/healthz", "/readyz"}, (
        f"these answer without a credential: {sorted(unauthenticated)}. FR-059 "
        f"exempts liveness and readiness and nothing else — a probe cannot carry "
        f"a key, and everything that can, must"
    )


def test_the_viewer_is_reachable_with_a_credential(client: TestClient) -> None:
    """Gating must not mean breaking: a caller holding a key still gets the page."""
    assert client.get("/ui", headers=bearer("acme")).status_code == 200


def test_with_authentication_off_nothing_requires_a_credential(tmp_path: Path) -> None:
    """FR-088. The default deployment is Milestone 8, including `/ui`.

    The pair of assertions matters more than either alone: gating `/ui` when
    authentication is *on* would be a regression if it also gated it when
    authentication is off, and that is a deployment nobody configured.
    """
    from docdoc.api.auth import KeyRing

    open_client = TestClient(
        build_app(
            _Deployment(
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                keys=KeyRing.disabled(),
            )
        )
    )

    refused = [
        path for path in _every_path(open_client.app) if open_client.get(path).status_code == 401
    ]
    assert not refused, f"a deployment with authentication off refused {refused}"


# -- US5/AC3: the run-submission route (T109) ---------------------------------
#
# "**Given** a blob submitted by tenant A, **When** tenant B submits a run
# against that blob identifier, **Then** the run is refused as though the blob
# did not exist."
#
# The tests above cover the *read* routes and the synchronous extract route. They
# never covered `POST /v1/documents/{blob_id}/runs` — which is the route this
# milestone added, and the one the scenario names. The code was right the whole
# time; nothing pinned it.


@pytest.fixture
def accepting(tmp_path: Path) -> TestClient:
    """Two tenants, one store root, and a run queue that works.

    The `client` fixture deliberately has none — `test_another_tenants_run_is_…`
    relies on that, because it asserts the answer for a deployment that accepts
    no runs at all. These two need one: without a queue the route answers 503
    from its `has_runs` check *before* it ever looks at the blob, so both tenants
    get the same 503 and the blob scoping is never exercised.

    That ordering is correct and worth recording: a deployment with no run
    database refuses every submission identically, which discloses nothing to
    anyone. It simply is not the scenario US5/AC3 describes.
    """
    from tests.fixtures.run_queue import InMemoryRunQueue

    return TestClient(
        build_app(
            _Deployment(
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                keys=KeyRing.from_file(Path(KEYS_FILE)),
                runs=InMemoryRunQueue(),
            )
        )
    )


def _submit_run(client: TestClient, tenant: str, blob_id: str) -> Any:
    return client.post(
        f"/v1/documents/{blob_id}/runs",
        params={"schema": SCHEMA},
        headers=bearer(tenant),
    )


def test_a_run_cannot_be_submitted_against_another_tenants_blob(
    accepting: TestClient,
) -> None:
    """US5/AC3, byte-identically to a blob that never existed.

    The asymmetry with the read routes is what makes this worth its own test:
    a *read* that leaked would disclose a document, and a *submission* that
    succeeded would execute one — a tenant would be spending its own provider
    budget on somebody else's invoice, and receiving the result.
    """
    blob_id = _submit(accepting, "acme")

    theirs = _submit_run(accepting, "globex", blob_id)
    nothing = _submit_run(accepting, "globex", ABSENT_ID)

    _identical(theirs, nothing, "run submission", asked=(blob_id, ABSENT_ID))
    assert theirs.status_code == 404


def test_the_owner_can_submit_a_run_against_the_same_blob(accepting: TestClient) -> None:
    """Guards the test above: a route that refused everyone would satisfy it.

    The owner gets 202 for the same identifier that answers 404 for the other
    tenant. Without this, scoping that had simply broken — every blob invisible
    to everybody — would pass the isolation assertion perfectly.
    """
    blob_id = _submit(accepting, "acme")

    mine = _submit_run(accepting, "acme", blob_id)

    assert mine.status_code == 202, (
        f"the owning tenant got {mine.status_code} for its own blob, so the "
        f"scoping is refusing everyone rather than refusing the other tenant"
    )
    assert mine.json()["run_id"]
