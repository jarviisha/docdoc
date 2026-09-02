"""The HTTP interface's configuration names, importable without FastAPI.

Separate from :mod:`docdoc.api.app` for one reason: a *name* is not a dependency.
``app`` imports FastAPI at module scope, as it must, so anything reading these
constants from there could only do so on an installation that has the ``api``
extra — and one of the things that reads them is the check asserting every
documented setting exists, which runs on a base install with no extras at all.

That check failed for thirteen documents once ``DOCDOC_MAX_REQUEST_BYTES`` was
added, and the fix is not to guard the check: it is that a caller asking "what is
this setting called?" should not have to install a web framework to find out.
"""

from __future__ import annotations

__all__ = [
    "DEFAULT_MAX_REQUEST_BYTES",
    "REQUEST_BYTES_ENV",
    "SCHEMA_PATHS_ENV",
    "STORE_ROOT_ENV",
    "UI_ROOT_ENV",
]

#: The same settings the CLI reads. One vocabulary, not two (FR-031).
STORE_ROOT_ENV = "DOCDOC_STORE_ROOT"
SCHEMA_PATHS_ENV = "DOCDOC_SCHEMA_PATHS"

#: Where the browser client's built assets are, when a deployment wants to say so
#: rather than let ``docdoc.api.ui`` find them. Research R7 kept this as the
#: fallback for a deployment that would rather build the interface itself than
#: install the ``docdoc-ui`` distribution; unset is the normal case.
UI_ROOT_ENV = "DOCDOC_UI_ROOT"

#: The request body cap, in bytes, applied while reading. Distinct from the
#: document size limit of ``ingest.Limits``: this one bounds what the *process*
#: will hold, and it has to fire before ingest can be consulted at all, because
#: by the time bytes reach ingest they are already in memory (research R10).
REQUEST_BYTES_ENV = "DOCDOC_MAX_REQUEST_BYTES"
DEFAULT_MAX_REQUEST_BYTES = 32 * 1024 * 1024


#: Where artifacts and blobs live, when that is an object store rather than a
#: directory. Milestone 9.
#:
#: ``DOCDOC_STORE_ROOT`` keeps its meaning and its precedence exactly: a
#: deployment that sets only the root behaves as it did under Milestone 8, which
#: is what SC-018 asserts. This is a second way to say where, not a replacement.
#:
#: Form: ``s3://bucket[/prefix][?endpoint_url=...]``. The query parameter exists
#: because MinIO and every other S3-compatible store needs one and AWS does not,
#: and putting it in the URL keeps "where the store is" a single value rather
#: than three variables that can disagree.
STORE_URL_ENV = "DOCDOC_STORE_URL"


def store_from_url(url: str, *, tenant_id: str = "default") -> tuple[object, object]:
    """Build ``(artifact_store, blob_store)`` from a store URL.

    Returns both because they are one decision: a deployment whose artifacts are
    in an object store and whose blobs are on a local disk has two halves of a
    store that cannot see each other, which is the multi-worker failure SC-005
    describes arriving by configuration instead of by omission.
    """
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url)
    if parsed.scheme != "s3":
        raise ValueError(
            f"unsupported store URL scheme {parsed.scheme!r}; use s3:// or set "
            f"{STORE_ROOT_ENV} for a local directory"
        )

    from docdoc.artifacts.s3 import S3ArtifactStore, S3BlobStore, s3_client

    endpoint = parse_qs(parsed.query).get("endpoint_url", [None])[0]
    client = s3_client(endpoint_url=endpoint)
    bucket = parsed.netloc
    prefix = parsed.path.strip("/")

    return (
        S3ArtifactStore(bucket, client=client, prefix=prefix, tenant_id=tenant_id),
        S3BlobStore(bucket, client=client, prefix=prefix, tenant_id=tenant_id),
    )
