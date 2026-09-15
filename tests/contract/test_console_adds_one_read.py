"""T039a — exactly one new route, it is a read, and FR-009's absences hold.

This is the test for the sentence Milestone 11 defines itself by. SC-003 says
the milestone adds **one** new API route and that it is a read; every plan,
contract, and research note in `specs/011` repeats it; and until this file
existed nothing counted. A milestone measured by a number that nobody computes is
measured by assertion.

It also carries FR-009 — no login route, no session record, no token exchange —
because that is the one absence claim in this spec that no other guard watches.
An unwatched absence claim is the failure mode every check in this repository
exists to answer.

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
ADDED = {("/v1/runs", "GET")}

#: Words a session mechanism arrives as (FR-009). None of them may name a route.
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


def test_the_surface_grew_by_exactly_one_route(client: TestClient) -> None:
    """SC-003, first half."""
    added = _routes(client) - MILESTONE_10
    removed = MILESTONE_10 - _routes(client)

    assert removed == set(), (
        f"Milestone 11 removed routes Milestone 10 shipped: {sorted(removed)}. "
        "Every write this console performs is supposed to reach a route that "
        "already existed"
    )
    assert added == ADDED, (
        f"the API surface grew by {sorted(added)}, and SC-003 claims exactly one "
        "new route. Either the addition is wrong, or the claim is — and the claim "
        "is what four documents in specs/011 are built on"
    )


def test_the_one_new_route_is_a_read(client: TestClient) -> None:
    """SC-003, second half. A write here would make it a new capability."""
    methods = {method for path, method in _routes(client) if path == "/v1/runs"}

    assert methods == {"GET"}


def test_no_route_names_a_session_mechanism(client: TestClient) -> None:
    """FR-009, and nothing else in this repository is watching it.

    The console holds a pasted key in memory for the life of a page. A login
    route would mean a session record, CSRF, and an expiry policy — four security
    surfaces written by this project to guard a credential the deployment already
    issues and already rotates (ADR-0019 §4).
    """
    for path, _ in _routes(client):
        for word in SESSION_WORDS:
            assert word not in path.lower(), (
                f"the route {path} names {word!r}. There is no session in this "
                "design: the credential is entered into the page and held there"
            )


def test_no_route_reports_the_callers_own_scope(client: TestClient) -> None:
    """Research R6 and R7, which both resolved to *ask by trying*.

    A route reporting the caller's credential identity, or whether the deployment
    authenticates at all, would each be a second new read — costing SC-003 — and
    the second would be an unauthenticated oracle about the deployment's posture.
    """
    for path, _ in _routes(client):
        assert not path.endswith("/me")
        assert "whoami" not in path.lower()
        assert "auth-mode" not in path.lower()


def test_the_console_mount_is_not_an_api_route(client: TestClient) -> None:
    """The shell is static assets, and it is exempt from the credential.

    It is asserted here to be absent from the `/v1` surface, so that "one new
    route" counts what a client can call and not what a browser loads.
    """
    assert not any(path.startswith("/console") for path, _ in _routes(client))
