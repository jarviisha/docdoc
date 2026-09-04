"""SC-018 — an existing deployment upgrades with zero configuration changes.

The regression this guards is the quiet one. Give the default tenant a prefix and
every Milestone 8 deployment's content moves to a path the new code never looks
at: the service comes up, every answer is correct, and it silently re-pays for
every parse — because a miss is indistinguishable from an absence. Nothing logs,
no metric moves, and the only evidence is the bill.

The specification's own first draft would have failed this. FR-084 said
namespacing was unconditional while SC-018 required pre-existing content to stay
readable, and the two sat in the same document until `/speckit-clarify` put them
side by side. FR-084a is the resolution and this file is its test.

Two halves, in the order an operator lives them:

* **before** enabling authentication — previously stored blobs and artifacts are
  readable at their original paths, reuse still hits, and the existing routes
  return what they returned;
* **after** enabling it — 0% of pre-existing content is unreachable, and nothing
  was copied or moved.
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
from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.artifacts.paths import DEFAULT_TENANT_ENV
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.pipeline import run as run_pipeline

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")
SCHEMA = "invoice@1"


def _registry() -> SchemaRegistry:
    return SchemaRegistry.from_paths([Path("schemas")])


def _adapter() -> EchoAdapter:
    return EchoAdapter.from_fixtures("tests/fixtures/echo")


def _seed_milestone_8_store(root: Path) -> tuple[str, str, set[Path]]:
    """Write a store the way Milestone 8 wrote one: no tenant anywhere.

    Deliberately built with the *stores*, not through the API. The layout being
    asserted is the one on disk, and going through routes that already namespace
    would seed whatever the new code happens to do rather than what the old code
    did.
    """
    pytest.importorskip("pymupdf")
    data = FIXTURE.read_bytes()

    blobs = BlobStore(root)
    blob_id = blobs.put(data)

    result = run_pipeline(
        data,
        schema=SCHEMA,
        registry=_registry(),
        adapter=_adapter(),
        store=FileArtifactStore(root),
    )
    assert result.processing_id is not None
    return blob_id, result.processing_id, _tree(root)


def _tree(root: Path) -> set[Path]:
    return {p.relative_to(root) for p in root.rglob("*") if p.is_file()}


def test_the_seeded_layout_has_no_tenant_segment(tmp_path: Path) -> None:
    """Guards every test below: a seed that already had a prefix proves nothing."""
    _seed_milestone_8_store(tmp_path)

    prefixed = [p for p in _tree(tmp_path) if p.parts and p.parts[0] == "t"]
    assert not prefixed, f"the seed is not a Milestone 8 layout: {prefixed}"
    assert any(p.parts[0] == "blobs" for p in _tree(tmp_path))
    assert any(p.parts[0] == "artifacts" for p in _tree(tmp_path))


# -- before: authentication at its default ------------------------------------


def test_pre_existing_content_is_readable_with_no_configuration_change(
    tmp_path: Path,
) -> None:
    """FR-084a, FR-088. The upgrade an operator does not notice."""
    blob_id, processing_id, seeded = _seed_milestone_8_store(tmp_path)

    client = TestClient(
        build_app(_Deployment(store_root=tmp_path, registry=_registry(), adapter=_adapter()))
    )

    assert client.get(f"/v1/documents/{blob_id}").status_code == 200
    assert client.get(f"/v1/jobs/{processing_id}").json()["status"] == "succeeded"
    assert client.get(f"/v1/jobs/{processing_id}/result").status_code == 200
    assert _tree(tmp_path) == seeded, "reading moved something"


def test_reuse_still_hits_over_pre_existing_artifacts(tmp_path: Path) -> None:
    """The half that fails *silently* if the layout moved.

    Every assertion above would pass on a deployment that had stranded its
    artifacts — the routes would answer 404 rather than wrongly, and a fresh run
    would recompute and look fine. This is the one that catches it: the run must
    execute **no** stage.
    """
    blob_id, _processing_id, _seeded = _seed_milestone_8_store(tmp_path)

    again = run_pipeline(
        FIXTURE.read_bytes(),
        schema=SCHEMA,
        registry=_registry(),
        adapter=_adapter(),
        store=FileArtifactStore(tmp_path),
    )

    assert again.executed_count == 0, (
        f"{again.executed_count} stages re-executed over a store that already "
        "held every artifact. The content was written at one path and is being "
        "looked for at another, which produces correct answers and a silent "
        "re-payment for every parse"
    )
    assert blob_id


# -- after: authentication enabled --------------------------------------------


def test_nothing_is_unreachable_after_authentication_is_enabled(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """SC-018's 0%, and FR-089's "assign, do not infer".

    `acme` is named in configuration as the owner of what was there before, so
    `acme`'s namespace *is* the root and its content is where it always was.
    Nothing is copied and nothing is moved — asserted on the file tree, because
    an assertion about reachability alone would pass a design that quietly
    duplicated everything.
    """
    blob_id, processing_id, seeded = _seed_milestone_8_store(tmp_path)

    monkeypatch.setenv(DEFAULT_TENANT_ENV, "acme")
    client = TestClient(
        build_app(
            _Deployment(
                store_root=tmp_path,
                registry=_registry(),
                adapter=_adapter(),
                keys=KeyRing.from_file(Path(KEYS_FILE)),
            )
        )
    )

    assert client.get(f"/v1/documents/{blob_id}", headers=bearer("acme")).status_code == 200
    assert (
        client.get(f"/v1/jobs/{processing_id}", headers=bearer("acme")).json()["status"]
        == "succeeded"
    )
    assert client.get(f"/v1/jobs/{processing_id}/result", headers=bearer("acme")).status_code == 200

    assert _tree(tmp_path) == seeded, (
        "enabling authentication copied or moved content. FR-084a forbids both: "
        "on an object store a move is a copy then a delete, and it would run for "
        "every operator including those who never enable authentication"
    )


def test_the_other_tenant_sees_none_of_it(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The assignment is to *one* tenant, which is what makes it an assignment.

    Without this, "nothing became unreachable" would be satisfiable by making
    everything reachable to everyone — which is the state before this milestone,
    not the state after it.
    """
    blob_id, processing_id, _seeded = _seed_milestone_8_store(tmp_path)

    monkeypatch.setenv(DEFAULT_TENANT_ENV, "acme")
    client = TestClient(
        build_app(
            _Deployment(
                store_root=tmp_path,
                registry=_registry(),
                adapter=_adapter(),
                keys=KeyRing.from_file(Path(KEYS_FILE)),
            )
        )
    )

    assert client.get(f"/v1/documents/{blob_id}", headers=bearer("globex")).status_code == 404
    assert (
        client.get(f"/v1/jobs/{processing_id}", headers=bearer("globex")).json()["status"]
        == "unavailable"
    )


def test_a_new_tenant_is_namespaced_under_the_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0014 §1's layout, asserted on disk rather than inferred from a 404."""
    _seed_milestone_8_store(tmp_path)
    monkeypatch.setenv(DEFAULT_TENANT_ENV, "acme")

    client = TestClient(
        build_app(
            _Deployment(
                store_root=tmp_path,
                registry=_registry(),
                adapter=_adapter(),
                keys=KeyRing.from_file(Path(KEYS_FILE)),
            )
        )
    )
    submitted: Any = client.post(
        "/v1/documents", content=FIXTURE.read_bytes(), headers=bearer("globex")
    )
    assert submitted.status_code == 200

    under_globex = [p for p in _tree(tmp_path) if p.parts[:2] == ("t", "globex")]
    assert under_globex, "the second tenant's blob did not land under t/globex/"
