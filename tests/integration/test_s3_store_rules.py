"""The S3 stores obey ADR-0010, against a real object store.

Six rows of a table, and none of them is new: §4's four read outcomes and §5's
two write outcomes were decided for the filesystem store, and this asserts they
survived the change of medium. A store that quietly differed on any of them would
be a second set of rules for the same guarantee.

The one that earns the most attention is the last: identical content written
twice is a no-op, and divergent content raises. That is what makes two workers
racing on the same artifact benign **without a lock**, which is the property the
whole multi-worker topology rests on.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from pydantic import BaseModel
from tests.infra import require_s3_endpoint

from docdoc.artifacts.errors import ArtifactError

pytestmark = pytest.mark.s3

BUCKET = "docdoc"


class Payload(BaseModel):
    """A stand-in for a stage result. The store never imports a real one."""

    value: str


@pytest.fixture
def store(request: pytest.FixtureRequest):
    """A store scoped to this test, so tests cannot see each other's objects."""
    pytest.importorskip("boto3")
    from docdoc.artifacts.s3 import S3ArtifactStore, s3_client

    endpoint = require_s3_endpoint()
    client = s3_client(
        endpoint_url=endpoint,
        aws_access_key_id="docdoc",
        aws_secret_access_key="docdocdocdoc",
    )
    prefix = f"test/{request.node.name}"

    # Emptied first, because an object store persists between runs and one of
    # these tests deliberately corrupts an artifact. Without this the suite
    # passes on a fresh bucket and fails on every run after it — the corrupted
    # object is still there, and `put` raises on reading it before the test that
    # meant to corrupt it has done anything.
    _empty(client, prefix)
    return S3ArtifactStore(BUCKET, client=client, prefix=prefix)


def _empty(client, prefix: str) -> None:
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=BUCKET, Prefix=prefix):
        for entry in page.get("Contents", ()):
            client.delete_object(Bucket=BUCKET, Key=entry["Key"])


def _put(store, artifact_id: str, value: str, *, stage: str = "extract") -> None:
    store.put(
        artifact_id,
        Payload(value=value),
        stage=stage,
        input_artifact_id=None,
        processor_id="test",
        processor_version="1",
        options_hash="sha256:" + "0" * 64,
        artifact_format_version=1,
    )


ID_A = "sha256:" + "a" * 64
ID_B = "sha256:" + "b" * 64


def test_nothing_stored_is_a_miss_and_not_an_error(store) -> None:
    """Row one. The stage executes; that is the whole of what a miss means."""
    assert store.get(ID_A, model=Payload, artifact_format_version=1) is None
    assert store.envelope(ID_A) is None


def test_an_incompatible_format_version_is_a_miss(store) -> None:
    """Row two. ADR-0010 §4: an upgrade must not break every run at once."""
    _put(store, ID_A, "one")

    assert store.get(ID_A, model=Payload, artifact_format_version=2) is None
    assert store.get(ID_A, model=Payload, artifact_format_version=1) == Payload(value="one")


def test_a_content_id_mismatch_raises_rather_than_recomputing(store) -> None:
    """Row three, and the one row that is not a miss.

    Recomputing over a corrupt artifact would hide a failing disk behind a
    slightly slower run. The distinction ADR-0010 §4 draws is between *this
    artifact does not apply to me* and *this artifact is not what it claims to
    be*, and only the second is a lie.
    """
    _put(store, ID_A, "one")

    # Corrupt the payload while leaving the recorded content id alone — exactly
    # what a partial write or a bit flip looks like from the reader's side.
    key = store._key_for(ID_A)
    raw = json.loads(store._client.get_object(Bucket=BUCKET, Key=key)["Body"].read())
    raw["payload"]["value"] = "tampered"
    store._client.put_object(Bucket=BUCKET, Key=key, Body=json.dumps(raw).encode("utf-8"))

    with pytest.raises(ArtifactError, match="content id"):
        store.envelope(ID_A)


def test_an_unreachable_store_degrades_rather_than_failing(store) -> None:
    """Row four. FR-063: the result is computed and correct; losing a cache
    entry is not a reason to lose it."""
    from docdoc.artifacts.s3 import S3ArtifactStore, s3_client

    unreachable = S3ArtifactStore(
        BUCKET,
        client=s3_client(
            endpoint_url="http://127.0.0.1:1",
            aws_access_key_id="x",
            aws_secret_access_key="y",
        ),
    )

    assert unreachable.envelope(ID_A) is None
    # And a write degrades too, rather than raising into a run that succeeded.
    _put(unreachable, ID_A, "one")


def test_identical_content_written_twice_is_a_no_op(store) -> None:
    """Row five. The benign race, and the reason no lock is needed."""
    _put(store, ID_A, "one")
    _put(store, ID_A, "one")

    assert store.get(ID_A, model=Payload, artifact_format_version=1) == Payload(value="one")


def test_divergent_content_under_one_identity_raises(store) -> None:
    """Row six. A processor whose output moved while its version did not.

    In general that failure is undetectable; here the evidence exists, and
    refusing the write turns a silent stale reuse into a loud error at the moment
    it happens (ADR-0010 §5).
    """
    _put(store, ID_A, "one")

    with pytest.raises(ArtifactError, match="different result"):
        _put(store, ID_A, "two")


def test_concurrent_identical_writes_all_succeed(store) -> None:
    """§5's concurrency sentence, against a store that really is concurrent.

    "Atomic replacement of an immutable, content-addressed entry is what makes
    the race benign." Eight writers, one identity, identical bytes: no lock, no
    lease, no coordinator, and no failure.
    """
    with ThreadPoolExecutor(max_workers=8) as pool:
        errors = [
            error
            for error in pool.map(lambda _: _try_put(store, ID_B, "same"), range(8))
            if error is not None
        ]

    assert not errors, f"identical concurrent writes raised {errors}"
    assert store.get(ID_B, model=Payload, artifact_format_version=1) == Payload(value="same")


def _try_put(store, artifact_id: str, value: str) -> str | None:
    try:
        _put(store, artifact_id, value)
    except ArtifactError as error:  # pragma: no cover - the assertion reports it
        return type(error).__name__
    return None


def test_the_default_tenant_writes_where_milestone_8_wrote(store) -> None:
    """FR-084a, at the level of the key that is actually used.

    The default tenant's namespace is the store root. An unconditional `t/`
    prefix would put an existing deployment's objects where the new code never
    looks — correct answers, and a silent re-payment for every parse.
    """
    from docdoc.artifacts.s3 import S3ArtifactStore

    default = S3ArtifactStore(BUCKET, client=store._client, prefix="p")
    other = S3ArtifactStore(BUCKET, client=store._client, prefix="p", tenant_id="acme")

    assert default._key_for(ID_A) == f"p/artifacts/{'a' * 2}/{'a' * 64}.json"
    assert other._key_for(ID_A) == f"p/t/acme/artifacts/{'a' * 2}/{'a' * 64}.json"


def test_a_blob_round_trips_and_a_missing_one_is_none() -> None:
    pytest.importorskip("boto3")
    from docdoc.artifacts.s3 import S3BlobStore, s3_client

    blobs = S3BlobStore(
        BUCKET,
        client=s3_client(
            endpoint_url=require_s3_endpoint(),
            aws_access_key_id="docdoc",
            aws_secret_access_key="docdocdocdoc",
        ),
        prefix="test/blobs",
    )
    data = Path("tests/fixtures/pdf/digital_invoice.pdf").read_bytes()

    blob_id = blobs.put(data)
    assert blobs.get(blob_id) == data
    assert blobs.size_of(blob_id) == len(data)
    assert blobs.get("sha256:" + "f" * 64) is None
