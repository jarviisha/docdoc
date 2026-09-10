"""T159, FR-083, FR-084 — the absence is the contract.

Principle IX permits the correction **model** and forbids the MVP becoming a
review **platform**, and the constitution's deferred-technology list names "a
full review UI" in as many words. That is a claim about routes that do not exist,
so it needs an exhaustive check: the difference between "we did not build an
assignment system" and "we have not built one yet" is one route, and nothing in a
diff makes that route look like a change of scope.

The two routes this milestone does add are a `POST` that records a correction and
a `GET` that reads this tenant's. Everything a platform would need beyond them —
somewhere to assign one, somewhere to queue it, a state to move it through, and a
way to edit the result — is asserted absent here by name.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from tests.fixtures.run_queue import InMemoryRunQueue

from docdoc.api.app import _Deployment, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters import EchoAdapter


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    return TestClient(
        build_app(
            _Deployment(
                store=FileArtifactStore(tmp_path),
                blobs=BlobStore(tmp_path),
                store_root=tmp_path,
                registry=SchemaRegistry.from_paths([Path("schemas")]),
                adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
                runs=InMemoryRunQueue(),
            )
        )
    )


def _paths(client: TestClient) -> dict:
    return client.app.openapi()["paths"]  # type: ignore[attr-defined]


#: What a review platform is made of. Each would be a reasonable next step, and
#: each is the step this milestone is not taking.
PLATFORM_WORDS = (
    "assign",
    "assignment",
    "reviewer",
    "reviewers",
    "queue",
    "worklist",
    "work-list",
    "inbox",
    "task",
    "tasks",
    "approve",
    "approval",
    "reject",
    "escalate",
)


@pytest.mark.parametrize("word", PLATFORM_WORDS)
def test_no_route_names_a_review_workflow(client: TestClient, word: str) -> None:
    """FR-083. Not one of them, on any method."""
    offending = [path for path in _paths(client) if word in path.lower()]

    assert not offending, (
        f"a route naming {word!r} exists: {offending}. Principle IX permits the "
        f"correction model and forbids the platform, and the deferred-technology "
        f"list names 'a full review UI'"
    )


def test_the_correction_routes_are_exactly_two(client: TestClient) -> None:
    """Record one, read this tenant's. There is no third."""
    correction_routes = {
        (method.upper(), path)
        for path, methods in _paths(client).items()
        for method in methods
        if "correction" in path
    }

    assert correction_routes == {
        ("POST", "/v1/runs/{run_id}/corrections"),
        ("GET", "/v1/runs/{run_id}/corrections"),
    }


def test_a_correction_cannot_be_edited_or_withdrawn(client: TestClient) -> None:
    """No `PATCH`, no `PUT`, and no `DELETE`.

    A correction is a reviewer's statement at a moment, and one that could be
    edited later would make the record of who said what unreadable. Withdrawing
    one is a second correction, which the `POST` already expresses.
    """
    methods = set(_paths(client).get("/v1/runs/{run_id}/corrections", {}))

    assert methods == {"post", "get"}


def test_no_route_edits_a_result(client: TestClient) -> None:
    """FR-078's structural half.

    A correction alters nothing it annotates. If a `PATCH` on a result existed,
    the recorded pipeline output would become a function of who reviewed it and
    the ADR-0003 identity chain would describe a run that never happened.
    """
    for path, methods in _paths(client).items():
        assert "patch" not in methods, f"{path} accepts PATCH"
        if "jobs" in path or "result" in path:
            assert set(methods) <= {"get"}, f"{path} is writable: {sorted(methods)}"


def test_the_viewer_has_no_write_path(client: TestClient) -> None:
    """FR-084. The viewer stays read-only and gains nothing from this milestone."""
    for path, methods in _paths(client).items():
        if path.startswith("/ui"):
            assert set(methods) <= {"get"}, f"{path} accepts {sorted(methods)}"


def test_there_is_no_review_state_on_a_run(client: TestClient) -> None:
    """`RunStatus` gains no `needs_review` (FR-004a), and no route invents one.

    Routing reports an *outcome* on a completed run; it does not put the run into
    a state, because a state would be a thing a workflow moves out of.
    """
    from docdoc.runs.model import RunStatus

    assert {member.value for member in RunStatus} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }


def test_this_check_can_actually_fail(client: TestClient) -> None:
    """Guards the guard: a path scan that saw nothing would pass every case."""
    assert "/v1/runs/{run_id}/corrections" in _paths(client)
