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

from typing import TYPE_CHECKING

from docdoc.runs import identity as _identity

if TYPE_CHECKING:
    from datetime import timedelta

__all__ = [
    "API_KEYS_FILE_ENV",
    "DEFAULT_MAX_REQUEST_BYTES",
    "REQUEST_BYTES_ENV",
    "RUN_DATABASE_URL_ENV",
    "RUN_LEASE_SECONDS_ENV",
    "RUN_MAX_ATTEMPTS_ENV",
    "SCHEMA_PATHS_ENV",
    "STORE_ROOT_ENV",
    "STORE_URL_ENV",
    "UI_ROOT_ENV",
    "run_lease",
    "run_max_attempts",
    "store_from_url",
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


#: Where run state lives. **No default**, for the reason ``DOCDOC_STORE_ROOT``
#: has none: where state accumulates is an operator's decision, and a service
#: that invented a database to write to would be making it for them. Unset is a
#: supported configuration and not a degraded one — the synchronous routes need
#: no database, so a Milestone 8 install upgrades untouched (SC-018).
#:
#: Spelled here as a literal although ``docdoc.cli.config`` defines the same
#: name. The two front ends are declared independent of each other, so neither
#: can import the other's constant, and the alternative — a shared module that
#: exists only to hold one string — buys nothing the duplication does not. The
#: cost is paid by ``test_documented_api_references_resolve.py``, which reads
#: both and would report a rename in either.
RUN_DATABASE_URL_ENV = "DOCDOC_RUN_DATABASE_URL"

#: How long a worker's claim holds before another may take the run, in seconds,
#: and how many times a run may be claimed before it is abandoned. Read by the
#: worker; the API writes runs and claims none, so neither changes a response.
#:
#: They are named here beside the database URL because they are one subject —
#: "how run state behaves" — and splitting a deployment's run configuration
#: across two modules by which process happens to read each is how a setting
#: ends up documented in neither. The values are imported from the layer that
#: reads them rather than restated, so a rename cannot leave this file wrong.
RUN_LEASE_SECONDS_ENV = _identity.LEASE_SECONDS_ENV
RUN_MAX_ATTEMPTS_ENV = _identity.MAX_ATTEMPTS_ENV

#: The key file that turns authentication on. **Absent means off** (FR-088), and
#: that is the compatible default rather than the safe one — see ADR-0014 §6 and
#: the warning the README is required to keep.
#:
#: A file rather than a variable holding the keys, because a key set is a list
#: and because file permissions are a control the environment does not offer
#: (research R14). A credential is never a flag: ``argv`` is readable by every
#: process on the host.
API_KEYS_FILE_ENV = "DOCDOC_API_KEYS_FILE"


def run_lease(explicit: int | None = None) -> timedelta:
    """The claim duration: explicit argument, then environment, then default.

    A delegate to ``docdoc.runs.identity``, which owns both defaults and is below
    both front ends. The worker reads this setting and the worker is a CLI
    subcommand, so a precedence rule implemented here would be one
    ``docdoc.cli`` could not call — the two front ends are declared independent.
    """
    from docdoc.runs.identity import configured_lease

    return configured_lease(explicit)


def run_max_attempts(explicit: int | None = None) -> int:
    """How many claims a run gets before it is abandoned. Same precedence."""
    from docdoc.runs.identity import configured_max_attempts

    return configured_max_attempts(explicit)


def store_from_url(url: str, *, tenant_id: str = "default") -> tuple[object, object]:
    """``(artifact_store, blob_store)`` from a store URL.

    A delegate. The implementation lives in ``docdoc.artifacts.s3`` because the
    worker needs it too and ``docdoc.cli`` cannot import ``docdoc.api`` — the two
    front ends are declared independent. Kept here under its original name
    because the HTTP layer's callers already use it.
    """
    from docdoc.artifacts.s3 import stores_from_url

    return stores_from_url(url, tenant_id=tenant_id)
