"""T017 — the shell loads without a credential, and nothing else does.

**This is the test that catches the mistake this milestone was most likely to
make.** `_mount_ui` gates the viewer behind the API credential and records the
consequence in a comment: with authentication on, the viewer *"does not load"*.
For the viewer that is the honest failure. For a console whose entire premise is
that an operator pastes a key **into a page that has already loaded** it is
fatal — a browser's top-level navigation carries no `Authorization` header and
cannot be made to.

So a console written by copying the viewer's mount would pass every other test in
this suite and be unusable on every deployment it exists for. That failure is
invisible to anyone developing against an unauthenticated deployment, which is
what everybody does locally. Hence a test.

The other half is the one that keeps the exemption honest: the shell is open and
the **data** is not. specs/011 research R1, ADR-0019.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.runs.principal import digest_of

KEY = "an-operations-key"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    """An **authenticated** deployment. The default would prove nothing here."""
    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps({"keys": [{"sha256": digest_of(KEY), "tenant_id": "acme"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))
    return TestClient(build_app(_Deployment(store_root=tmp_path)))


def test_the_console_shell_loads_with_no_credential(client: TestClient) -> None:
    """FR-001, FR-006. The page has to exist before a key can be typed into it."""
    response = client.get("/console/")

    assert response.status_code != 401, (
        "the console's shell is behind the credential, so it can never load on an "
        "authenticated deployment — which is every deployment it is for. The key "
        "is entered *into* this page (FR-006); see research R1"
    )
    # 501 when the assets are not built, 200 when they are. Both are "not
    # refused", which is the property under test; T072 covers the built case end
    # to end.
    assert response.status_code in {200, 501}


def test_the_bare_console_path_is_exempt_too(client: TestClient) -> None:
    response = client.get("/console")
    assert response.status_code != 401


def test_the_data_is_still_behind_the_credential(client: TestClient) -> None:
    """The exemption is the shell and nothing else."""
    for path in ("/v1/runs", "/v1/schemas", "/v1/admin/credentials"):
        assert client.get(path).status_code == 401, f"{path} answered without a credential"


def test_the_viewer_is_unchanged(client: TestClient) -> None:
    """FR-022b. Milestone 8's posture is what it was, including its gate."""
    assert client.get("/ui/").status_code == 401


def test_a_path_that_merely_starts_with_console_is_not_exempt(client: TestClient) -> None:
    """The prefix is `/console/`, not the string `console`.

    Checked because the cheap implementation of the exemption is
    `path.startswith("/console")`, which would also exempt `/consoleroom` — and a
    route added under such a name years from now would be silently open.
    """
    assert client.get("/consoleroom").status_code == 401


def test_the_listing_is_reachable_with_a_credential(client: TestClient) -> None:
    """The pair to the test above: refused without, answered with."""
    response = client.get("/v1/runs", headers={"Authorization": f"Bearer {KEY}"})
    # 503 because this deployment records no runs; the point is that it is not a
    # 401, so the credential was accepted and the route exists.
    assert response.status_code == 503
    assert response.json()["error"]["class"] == "RunStateUnavailableError"
