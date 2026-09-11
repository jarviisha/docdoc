"""T084, T086 — what a caller without the scope sees, and what an upgrade changes.

**404 and not 403** (ADR-0016 §6). A `403` tells a caller that an administrative
surface exists at this URL and that their key is the only thing missing. That is a
disclosure with no upside, and it is the same reasoning `AuthenticationError`
already follows in refusing to distinguish absent from malformed from
unrecognised.

**And a Milestone 9 deployment reaches none of it** (FR-038, SC-002). The file
ring builds principals with no scopes, so a deployment that upgrades and changes
nothing has no way in — which is the direction that matters. Erasing a tenant on a
deployment that has never named one is not an operation anybody should be able to
invoke over HTTP.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.runs.principal import digest_of

NOW = __import__("datetime").datetime(2026, 9, 4, 12, 0, tzinfo=__import__("datetime").UTC)

ADMIN_ROUTES = (
    ("post", "/v1/admin/credentials"),
    ("get", "/v1/admin/credentials?tenant_id=acme"),
    ("delete", "/v1/admin/credentials/3f4a1b2c-0000-4000-8000-000000000000"),
    ("delete", "/v1/admin/tenants/acme"),
    ("delete", "/v1/admin/documents/sha256:abc"),
)


@pytest.fixture
def tenant_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Authentication on, with one ordinary tenant key and no admin anywhere."""
    keys = tmp_path / "keys.json"
    keys.write_text(
        json.dumps({"keys": [{"sha256": digest_of("tenant-key"), "tenant_id": "acme"}]}),
        encoding="utf-8",
    )
    monkeypatch.setenv("DOCDOC_API_KEYS_FILE", str(keys))
    return "tenant-key"


@pytest.fixture
def client() -> TestClient:
    """No registry and no adapter, deliberately.

    An admin route reads neither, and building them here would make this file
    look — to `test_plan_tree_is_current.py`, and to a reader — like it exercises
    the extraction layer. It exercises one dependency: who is allowed through.
    """
    return TestClient(build_app(_Deployment()))


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_a_tenant_key_gets_404_and_never_403(
    client: TestClient, tenant_key: str, method: str, path: str
) -> None:
    response = getattr(client, method)(path, headers={"authorization": f"Bearer {tenant_key}"})

    assert response.status_code == 404, (
        f"{method.upper()} {path} answered {response.status_code}. A 403 tells a "
        "caller that an administrative surface is here and that their key is the "
        "only thing missing (ADR-0016 section 6)."
    )


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_a_milestone_9_deployment_reaches_none_of_it(
    client: TestClient, tenant_key: str, method: str, path: str
) -> None:
    """FR-038: the file ring grants no scopes, so upgrading opens no door."""
    assert (
        getattr(client, method)(path, headers={"authorization": f"Bearer {tenant_key}"})
    ).status_code == 404


@pytest.mark.parametrize(("method", "path"), ADMIN_ROUTES)
def test_an_unauthenticated_deployment_reaches_none_of_it_either(
    client: TestClient, method: str, path: str
) -> None:
    """Authentication off is one implicit tenant with no scopes (FR-088).

    Which is correct rather than restrictive: erasing "a tenant" on a deployment
    that has never named one means erasing everything, and that reading exists on
    the command line behind a flag somebody has to type.
    """
    assert getattr(client, method)(path).status_code == 404


def test_the_ordinary_routes_are_untouched(client: TestClient, tenant_key: str) -> None:
    """SC-002: the same key that cannot reach an admin route still works."""
    response = client.get("/v1/schemas", headers={"authorization": f"Bearer {tenant_key}"})

    assert response.status_code == 200


class TestTheErasureRoutesActuallyErase:
    """The assertion whose absence let a no-op ship.

    A code review found that both erasure routes returned `202` and did nothing:
    they minted an identifier, discarded the request, and reported acceptance.
    Nothing failed, because nothing checked. The route existed, the task was
    marked done, and the response looked exactly like success.

    That is the failure spec.md names by hand — *"a deployment that answers
    'erased' while the bytes are still in the bucket: an answer that is worse
    than refusing, because it is believed"* — arriving through the route rather
    than through the sweep.

    So this asserts an **effect**, not a status code.
    """

    @pytest.fixture
    def erasing(self, tmp_path: Path) -> tuple[TestClient, object, str]:
        """A deployment with an administrator, which is the only way in.

        Built rather than borrowed: with authentication off every principal is
        the default tenant with no scopes, so the admin routes are unreachable —
        which the class above asserts on purpose. Reaching them requires holding
        a credential that carries the scope, and this is the smallest thing that
        does.
        """
        from tests.fixtures.run_queue import InMemoryRunQueue

        from docdoc.artifacts import BlobStore, FileArtifactStore
        from docdoc.runs.principal import ADMIN_SCOPE, Principal

        class AdminRing:
            enabled = True

            def resolve(self, digest: str) -> Principal:
                return Principal(tenant_id="ops", scopes=frozenset({ADMIN_SCOPE}))

            def principal_for(self, credential: str | None) -> Principal:
                return self.resolve("")

        queue = InMemoryRunQueue()
        blob_id = BlobStore(tmp_path, tenant_id="acme").put(b"a customer's document")
        client = TestClient(
            build_app(
                _Deployment(
                    store=FileArtifactStore(tmp_path, tenant_id="acme"),
                    blobs=BlobStore(tmp_path, tenant_id="acme"),
                    store_root=tmp_path,
                    runs=queue,
                    keys=AdminRing(),
                )
            )
        )
        return client, queue, blob_id

    def test_a_tenant_erasure_removes_the_tenants_runs(
        self, erasing: tuple[TestClient, object, str], tmp_path: Path
    ) -> None:
        from uuid import uuid4

        from docdoc.runs.model import Run, RunStatus

        client, queue, blob_id = erasing
        run = Run(
            run_id=uuid4(),
            tenant_id="acme",
            blob_id=blob_id,
            schema_identity="invoice@1",
            status=RunStatus.SUCCEEDED,
            processing_id="sha256:" + "cd" * 32,
            created_at=NOW,
            updated_at=NOW,
            expires_at=NOW,
        )
        queue._runs[run.run_id] = run  # type: ignore[attr-defined]

        response = client.delete("/v1/admin/tenants/acme", headers={"authorization": "Bearer x"})

        assert response.status_code == 200, response.text
        assert response.json()["runs"] == 1, (
            "the route reported acceptance and removed nothing. An erasure that "
            "answers without acting is worse than one that refuses, because it "
            "is believed."
        )
        assert queue.get(run.run_id, "acme") is None  # type: ignore[attr-defined]
        assert queue.tombstone(run.run_id, "acme") is not None  # type: ignore[attr-defined]

    def test_erasing_a_tenant_that_never_existed_succeeds(
        self, erasing: tuple[TestClient, object, str]
    ) -> None:
        """FR-009 — an operator under time pressure runs this twice."""
        client, _queue, _blob = erasing

        response = client.delete("/v1/admin/tenants/nobody", headers={"authorization": "Bearer x"})

        assert response.status_code == 200
        assert response.json()["runs"] == 0

    def test_the_default_tenant_is_still_refused(
        self, erasing: tuple[TestClient, object, str]
    ) -> None:
        client, _queue, _blob = erasing

        response = client.delete("/v1/admin/tenants/default", headers={"authorization": "Bearer x"})

        assert response.status_code == 409
        assert response.json()["error"] == "default_tenant_erasure_refused"
