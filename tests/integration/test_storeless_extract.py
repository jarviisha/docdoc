"""``POST /v1/extract`` writes nothing. Counted, not asserted in prose.

Three places a run could leave something behind, and all three are checked: the
artifact store, the blob store, and the process's temporary directory. The last
one matters because "wrote nothing" is easy to satisfy for the two stores and
easy to miss for a parser that spools a PDF to disk on its way through.

**The second test is the one worth keeping.** A deployment with no store cannot
write, so proving it wrote nothing proves little. A deployment *with* a perfectly
good store, on the same route, must also write nothing — because whether a run
persists is a property of the endpoint the caller chose and never of the
configuration (FR-008). That is the property a later change breaks for a
plausible reason: the store is right there, and reusing it looks like an
optimisation.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

pytest.importorskip("fastapi", reason="the HTTP interface lives behind the docdoc[api] extra")

from fastapi.testclient import TestClient

from docdoc.api.app import _Deployment, build_app
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"


def _deployment(root: Path | None = None) -> _Deployment:
    return _Deployment(
        store=None if root is None else FileArtifactStore(root),
        blobs=None if root is None else BlobStore(root),
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
    )


def _files(root: Path) -> set[Path]:
    return {path for path in root.rglob("*") if path.is_file()}


@pytest.fixture
def scratch(tmp_path_factory: pytest.TempPathFactory, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A temporary directory nothing outside this test can write to.

    **These tests used to read the whole of ``tempfile.gettempdir()``** and treat
    any new entry as something the run had written. That made SC-007 and SC-008
    assertions about the machine rather than about docdoc: two runs here failed on
    ``/tmp/rustdoctesttz24rn/rustdoc-cfgs``, left by an unrelated ``rustdoc``
    process, while the same tests passed in isolation and the full suite passed on
    a clean re-run. On a shared CI runner that is a spurious failure on the two
    criteria that matter most about the storeless path (T106).

    Redirecting ``tempfile`` here keeps the check strictly stronger than a
    narrower one would be: anything docdoc spools through ``tempfile`` lands in
    this directory, so an empty directory is still evidence that nothing was
    written — it just stops being evidence about everyone else's processes too.
    """
    # Deliberately not under the test's own ``tmp_path``: that is the store's
    # directory in the second test, and the two assertions must stay about
    # different things — one about what the store holds, one about what was
    # spooled and left behind.
    private = tmp_path_factory.mktemp("scratch")

    monkeypatch.setattr(tempfile, "tempdir", str(private))
    for name in ("TMPDIR", "TEMP", "TMP"):
        monkeypatch.setenv(name, str(private))

    assert Path(tempfile.gettempdir()) == private
    return private


def _run(client: TestClient) -> None:
    response = client.post("/v1/extract", params={"schema": SCHEMA}, content=FIXTURE.read_bytes())
    assert response.status_code == 200, response.text


def test_a_run_with_no_store_configured_leaves_nothing_anywhere(scratch: Path) -> None:
    """FR-002, SC-007 — no blob, no artifact, no temporary file."""
    client = TestClient(build_app(_deployment()))
    before = _files(scratch)

    _run(client)

    assert _files(scratch) - before == set()


def test_a_run_with_a_store_configured_still_leaves_nothing(tmp_path: Path, scratch: Path) -> None:
    """FR-008, SC-008 — the endpoint decides, not the deployment.

    If this test ever fails, read the diff for a call to
    ``deployment.artifact_store()`` in the storeless route. That is the change
    this test exists to catch, and it will have looked entirely reasonable.
    """
    client = TestClient(build_app(_deployment(tmp_path)))
    before_store, before_temp = _files(tmp_path), _files(scratch)

    _run(client)

    assert _files(tmp_path) == before_store
    assert _files(scratch) - before_temp == set()


def test_the_store_backed_route_on_the_same_deployment_does_write(tmp_path: Path) -> None:
    """The control.

    Without this, the two tests above are satisfied by a store that never worked.
    The same deployment, the other route, must leave artifacts behind — which is
    what makes their emptiness evidence about the endpoint rather than about the
    fixture.
    """
    client = TestClient(build_app(_deployment(tmp_path)))
    source = FIXTURE.read_bytes()

    blob_id = client.post("/v1/documents", content=source).json()["blob_id"]
    response = client.post(f"/v1/documents/{blob_id}/extract", params={"schema": SCHEMA})

    assert response.status_code == 200, response.text
    assert _files(tmp_path), "the store-backed route wrote nothing either — the store is broken"
