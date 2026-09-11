"""T031, T032 — the store's two new verbs, and the guard on the dangerous one.

Two properties, and the second is the one worth having.

**`delete` reports presence** (FR-012), so a sweep counts what it removed rather
than what it attempted. A count that includes things already gone is a count
nobody can act on.

**`delete_prefix` refuses the store root.** ADR-0014 §3 makes the default
tenant's namespace the root itself, and ADR-0014's own Consequences section
predicted this milestone would build the naive version: *"a naive 'delete tenant'
implementation in Milestone 10 would delete everything."* This file is that
prediction, as a test.

What it removes is narrower than an early draft of ADR-0015 claimed and is still
the whole store from a deployment's point of view: `<root>/artifacts/` and
`<root>/blobs/`, which is everything written before authentication was enabled
and everything `docdoc extract` ever wrote. Other tenants live under `<root>/t/`
and survive — asserted below, because that is a property of the layout worth
knowing is true rather than assuming.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from docdoc.artifacts.blobs import BlobStore
from docdoc.artifacts.errors import ArtifactError
from docdoc.artifacts.store import FileArtifactStore, NullArtifactStore

if TYPE_CHECKING:
    import pathlib


class TestDeleteReportsPresence:
    def test_a_blob_is_deleted_once(self, tmp_path: pathlib.Path) -> None:
        blobs = BlobStore(tmp_path)
        blob_id = blobs.put(b"a document")

        assert blobs.delete(blob_id) is True
        assert blobs.delete(blob_id) is False
        assert blobs.get(blob_id) is None

    def test_a_null_store_deleted_nothing(self) -> None:
        """It holds nothing, so it removed nothing, and it does not raise.

        Raising would make this the one implementation a sweep has to branch for,
        which is how a caller ends up with a `hasattr` check instead of a
        protocol.
        """
        store = NullArtifactStore()

        assert store.delete("sha256:whatever") is False
        assert store.delete_prefix() == 0


class TestTheStoreRootIsGuarded:
    def test_the_default_tenant_is_refused(self, tmp_path: pathlib.Path) -> None:
        blobs = BlobStore(tmp_path)
        blobs.put(b"written before authentication existed")

        with pytest.raises(ArtifactError) as caught:
            blobs.delete_prefix()

        assert caught.value.reason == "store_root_refused"
        assert blobs.get(blobs.put(b"written before authentication existed")) is not None

    def test_the_artifact_store_refuses_on_the_same_terms(self, tmp_path: pathlib.Path) -> None:
        with pytest.raises(ArtifactError) as caught:
            FileArtifactStore(tmp_path).delete_prefix()

        assert caught.value.reason == "store_root_refused"

    def test_the_flag_is_what_permits_it(self, tmp_path: pathlib.Path) -> None:
        """One call site has it, on the command line, and no HTTP route does."""
        blobs = BlobStore(tmp_path)
        blobs.put(b"one")
        blobs.put(b"two")

        assert blobs.delete_prefix(allow_store_root=True) == 2

    def test_a_named_tenant_needs_no_flag(self, tmp_path: pathlib.Path) -> None:
        blobs = BlobStore(tmp_path, tenant_id="acme")
        blobs.put(b"acme's document")

        assert blobs.delete_prefix() == 1

    def test_erasing_a_named_tenant_leaves_the_others(self, tmp_path: pathlib.Path) -> None:
        """The layout does this, not the code — and it is worth asserting.

        `t/<tenant>/` sits outside `<root>/blobs/`, so one tenant's prefix delete
        cannot reach another's. ADR-0014 §1 put the tenant above the fan-out for
        the prefix operation's sake, and this is the property that buys.
        """
        acme = BlobStore(tmp_path, tenant_id="acme")
        globex = BlobStore(tmp_path, tenant_id="globex")
        default = BlobStore(tmp_path)

        acme_id = acme.put(b"same bytes")
        globex_id = globex.put(b"same bytes")
        default_id = default.put(b"same bytes")

        acme.delete_prefix()

        assert acme.get(acme_id) is None
        assert globex.get(globex_id) == b"same bytes"
        assert default.get(default_id) == b"same bytes"
