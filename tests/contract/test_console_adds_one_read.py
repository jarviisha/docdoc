"""T039a — exactly one new route, it is a read, and FR-009's absences hold.

This is the test for the sentence Milestone 11 defines itself by. SC-003 says
the milestone adds **one** new API route and that it is a read; every plan,
contract, and research note in `specs/011` repeats it; and until this file
existed nothing counted. A milestone measured by a number that nobody computes is
measured by assertion.

It also carries FR-009 — no login route, no session record, no token exchange —
because that is the one absence claim in this spec that no other guard watches.
An unwatched absence claim is the failure mode every check in this repository
exists to answer. ADR-0023 §1 has since given the console a login; §3 confines
it to `/v1/console/`, and that confinement is what this file now watches.

**How the "one" is counted**: against a list of the routes Milestone 10 shipped,
written out below. A snapshot rather than a computation, because the alternative
is comparing against a git revision, which makes the test unrunnable from a
shallow clone and turns a clear failure into an infrastructure problem.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.api.app import _Deployment, build_app

#: Every `/v1` route Milestone 10 left behind, by path and method.
#:
#: Written out rather than derived. If a future milestone adds a route, this list
#: is what it edits, and editing it is the moment somebody decides on purpose
#: that the surface grew — which is the decision this test exists to make visible.
MILESTONE_10 = {
    ("/v1/documents", "POST"),
    ("/v1/documents/{blob_id}", "GET"),
    ("/v1/documents/{blob_id}/extract", "POST"),
    ("/v1/documents/{blob_id}/runs", "POST"),
    ("/v1/extract", "POST"),
    ("/v1/runs/{run_id}", "GET"),
    ("/v1/runs/{run_id}", "DELETE"),
    ("/v1/runs/{run_id}/corrections", "POST"),
    ("/v1/runs/{run_id}/corrections", "GET"),
    ("/v1/runs/{run_id}/delivery", "GET"),
    ("/v1/callbacks", "POST"),
    ("/v1/callbacks/{callback_id}", "DELETE"),
    ("/v1/schemas", "GET"),
    ("/v1/jobs/{job_id}", "GET"),
    ("/v1/jobs/{job_id}/result", "GET"),
    ("/v1/admin/credentials", "POST"),
    ("/v1/admin/credentials", "GET"),
    ("/v1/admin/credentials/{credential_id}", "DELETE"),
    ("/v1/admin/tenants/{tenant_id}", "DELETE"),
    ("/v1/admin/documents/{blob_id}", "DELETE"),
}

#: What Milestone 11 is allowed to have added. One entry, and it is a `GET`.
#: `test_the_one_new_route_is_a_read` below still pins this half by itself,
#: scoped to `/v1/runs` alone -- that assertion is unaffected by anything a
#: later milestone adds.
MILESTONE_11_ADDED = {("/v1/runs", "GET")}

#: The OCR gateway (Task 5, ADR-0021), added on top of Milestone 11's own
#: surface: `POST /v1/ocr` is a write (it runs a parse), so it is additional to
#: SC-003's "exactly one read" claim rather than a second instance of it.
OCR_GATEWAY_ADDED = {("/v1/ocr", "POST"), ("/v1/parsers", "GET")}
#: 014 (ADR-0022): the queued OCR run's result. A read of an OCR run, not a
#: console route.
ASYNC_OCR_ADDED = {("/v1/runs/{run_id}/result", "GET")}
#: 015 (ADR-0023 §1, §3): the console's own route family, behind a session
#: rather than a Bearer key. Tasks 5 and 6 extend this set.
CONSOLE_ADDED = {
    ("/v1/console/session", "POST"),
    ("/v1/console/session", "GET"),
    ("/v1/console/session", "DELETE"),
    # ADR-0023 §3: run reads across tenants, all four reads.
    ("/v1/console/runs", "GET"),
    ("/v1/console/runs/{run_id}", "GET"),
    ("/v1/console/runs/{run_id}/delivery", "GET"),
    ("/v1/console/runs/{run_id}/result", "GET"),
    # Task 6 (ADR-0023 §2, §3): credentials and erasure, mirroring the admin
    # routes of the same shape behind the console session instead of a Bearer
    # key.
    ("/v1/console/credentials", "GET"),
    ("/v1/console/credentials", "POST"),
    ("/v1/console/credentials/{credential_id}", "DELETE"),
    ("/v1/console/tenants/{tenant_id}", "DELETE"),
    ("/v1/console/tenants/{tenant_id}/documents/{blob_id}", "DELETE"),
    # 017 (console monitoring plan, Task 2): the Overview read, and a cancel
    # mirroring `DELETE /v1/runs/{run_id}` under the run's owner.
    ("/v1/console/overview", "GET"),
    ("/v1/console/runs/{run_id}", "DELETE"),
    # 018 (console submit OCR plan, Task 1): the tenant and parser lists the
    # submit form needs, and OCR submission for a named tenant through the
    # same `submit_ocr` `POST /v1/ocr` calls.
    ("/v1/console/tenants", "GET"),
    ("/v1/console/parsers", "GET"),
    ("/v1/console/tenants/{tenant_id}/ocr", "POST"),
    # Console extraction submit, through the same `submit_extraction` the run
    # route's shared part serves.
    ("/v1/console/tenants/{tenant_id}/extractions", "POST"),
    # 019 (console OCR result plan, Task 1): an OCR run's page images, so the
    # result view can draw token boxes over the page they came from.
    ("/v1/console/runs/{run_id}/pages/{page}", "GET"),
    # 021 (console admin screens plan, Task 1): callbacks, per tenant --
    # register, revoke, and the listing `/v1/callbacks` itself does not have.
    ("/v1/console/tenants/{tenant_id}/callbacks", "GET"),
    ("/v1/console/tenants/{tenant_id}/callbacks", "POST"),
    ("/v1/console/tenants/{tenant_id}/callbacks/{callback_id}", "DELETE"),
}

#: Tenant quality evaluation: a tenant's answer counts, and evaluations of them.
EVALUATIONS_ADDED = {
    ("/v1/answers", "GET"),
    ("/v1/evaluations", "POST"),
    ("/v1/evaluations", "GET"),
    ("/v1/evaluations/{evaluation_id}", "GET"),
    ("/v1/console/runs/{run_id}/corrections", "POST"),
    ("/v1/console/runs/{run_id}/corrections", "GET"),
    ("/v1/console/tenants/{tenant_id}/answers", "GET"),
    ("/v1/console/tenants/{tenant_id}/evaluations", "POST"),
    ("/v1/console/tenants/{tenant_id}/evaluations", "GET"),
    ("/v1/console/tenants/{tenant_id}/evaluations/{evaluation_id}", "GET"),
}

#: Everything this interface has added over Milestone 10's own surface, as of
#: this milestone. A route added without a decision shows up here: this is the
#: list a later milestone edits, and editing it is the moment somebody decides
#: on purpose that the surface grew (the same argument `ROUTES` in
#: `test_http_ui_endpoints.py` makes for the full set).
ADDED = MILESTONE_11_ADDED | OCR_GATEWAY_ADDED | ASYNC_OCR_ADDED | CONSOLE_ADDED | EVALUATIONS_ADDED

#: Words a session mechanism arrives as (FR-009). Since ADR-0023 §3 they may
#: name a route only under `/v1/console/`.
SESSION_WORDS = ("login", "logout", "session", "token", "auth", "signin", "sign-in")


def _routes(client: TestClient) -> set[tuple[str, str]]:
    """Every documented `/v1` route, by path and method.

    Read from the OpenAPI document rather than by walking `app.routes`, which is
    what `test_no_review_platform.py` does and for a reason worth inheriting: the
    router is wrapped by the time it reaches the application, so walking it means
    knowing FastAPI's internals, and the published document is the surface a
    client actually sees.
    """
    document = client.app.openapi()  # type: ignore[attr-defined]
    return {
        (path, method.upper())
        for path, operations in document["paths"].items()
        if path.startswith("/v1")
        for method in operations
        if method.upper() not in {"HEAD", "OPTIONS"}
    }


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """A deployment with everything configured, so every route is registered.

    A partial deployment would hide routes and let the count pass by having
    fewer of them, which is the opposite of what this test is for.
    """
    return TestClient(build_app(_Deployment(store_root=tmp_path, runs=InMemoryRunQueue())))


def test_the_surface_matches_the_recorded_additions(client: TestClient) -> None:
    """SC-003, first half, plus everything recorded since.

    Originally "the surface grew by exactly one route" — true of Milestone 11
    alone, against Milestone 10's own baseline. A later milestone that adds a
    route is not a violation of that claim; it is a second, separate addition,
    and `ADDED` is where it is recorded so this assertion keeps meaning
    something rather than failing forever the first time anything is added.
    """
    added = _routes(client) - MILESTONE_10
    removed = MILESTONE_10 - _routes(client)

    assert removed == set(), (
        f"routes Milestone 10 shipped are gone: {sorted(removed)}. Every write "
        "this console performs is supposed to reach a route that already existed"
    )
    assert added == ADDED, (
        f"the API surface grew by {sorted(added)}, and only {sorted(ADDED)} is "
        "recorded as deliberate. Either the addition is wrong, or this list is — "
        "and the list is what SC-003 and Task 5's own brief are built on"
    )


def test_the_one_new_route_is_a_read(client: TestClient) -> None:
    """SC-003, second half. A write here would make it a new capability.

    Scoped to `/v1/runs` alone, so a later milestone's own routes — `/v1/ocr`
    is deliberately a write — cannot make this assertion mean something it
    never claimed.
    """
    methods = {method for path, method in _routes(client) if path == "/v1/runs"}

    assert methods == {"GET"}


def test_session_words_appear_only_under_the_console_api(client: TestClient) -> None:
    """FR-009, narrowed by ADR-0023 rather than dropped.

    ADR-0023 §1 superseded ADR-0019 §4: the console now signs in with a
    username and password and holds a session. §3 confines that session to its
    own route family, so a session mechanism is allowed exactly there and still
    nowhere else — a login word on any other `/v1` path would be a session
    leaking into the Bearer-key surface.
    """
    for path, _ in _routes(client):
        for word in SESSION_WORDS:
            if word in path.lower():
                assert path.startswith("/v1/console/"), (
                    f"the route {path} names {word!r} outside /v1/console/. "
                    "Sessions belong to the console's route family alone (ADR-0023 §3)"
                )


def test_no_route_reports_the_callers_own_scope(client: TestClient) -> None:
    """Research R6 and R7, which both resolved to *ask by trying*.

    A route reporting the caller's credential identity, or whether the deployment
    authenticates at all, would each be a second new read — costing SC-003 — and
    the second would be an unauthenticated oracle about the deployment's posture.
    """
    for path, _ in _routes(client):
        if path == "/v1/console/session":
            # ADR-0023 §1: a signed-in console asks who it is signed in as. It
            # reports a console username, never a Bearer credential's scope, and
            # answers nothing without a session -- not a posture oracle.
            continue
        assert not path.endswith("/me")
        assert "whoami" not in path.lower()
        assert "auth-mode" not in path.lower()


def test_the_console_mount_is_not_an_api_route(client: TestClient) -> None:
    """The shell is static assets, and it is exempt from the credential.

    It is asserted here to be absent from the `/v1` surface, so that "one new
    route" counts what a client can call and not what a browser loads.
    """
    assert not any(path.startswith("/console") for path, _ in _routes(client))
