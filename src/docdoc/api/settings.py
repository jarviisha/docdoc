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

from docdoc import telemetry as _telemetry
from docdoc.runs import identity as _identity

if TYPE_CHECKING:
    from datetime import timedelta

__all__ = [
    "API_KEYS_FILE_ENV",
    "CORRECTION_RETENTION_DAYS_ENV",
    "DEFAULT_MAX_REQUEST_BYTES",
    "DELIVERY_ALLOW_PRIVATE_ENV",
    "DELIVERY_ATTEMPTS_ENV",
    "DELIVERY_BACKOFF_ENV",
    "DELIVERY_BATCH_ENV",
    "DELIVERY_SECRETS_FILE_ENV",
    "DELIVERY_TIMEOUT_ENV",
    "LIMITS_FILE_ENV",
    "LIMIT_CONCURRENT_ENV",
    "LIMIT_RUNS_PER_PERIOD_ENV",
    "LIMIT_SUBMISSIONS_ENV",
    "LIMIT_TOKENS_ENV",
    "MAINTENANCE_BUDGET_ENV",
    "MAINTENANCE_INTERVAL_ENV",
    "OTLP_ENDPOINT_ENV",
    "OTLP_HEADERS_ENV",
    "PRIORITY_CEILING_ENV",
    "REQUEST_BYTES_ENV",
    "ROUTING_POLICY_ENV",
    "RUN_CREDENTIAL_TTL_ENV",
    "RUN_DATABASE_URL_ENV",
    "RUN_LEASE_SECONDS_ENV",
    "RUN_MAX_ATTEMPTS_ENV",
    "RUN_RETENTION_DAYS_ENV",
    "RUN_STARVATION_SECONDS_ENV",
    "RUN_SWEEP_BATCH_ENV",
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
#: Milestone 10, re-exported on the same terms as the two above: `identity` owns
#: the defaults and the precedence, and both front ends spell the names for
#: themselves rather than importing each other.
#: The four limits, all optional and all absent by default (FR-049). Named here
#: rather than in `identity` because they are a *deployment* policy rather than a
#: run's own tuning: the worker reads none of them.
#: Per-tenant overrides, on the same reasoning that makes the key ring a file
#: (research R14): a per-tenant table of anything is a list, and file permissions
#: are a control the environment does not offer.
LIMITS_FILE_ENV = "DOCDOC_LIMITS_FILE"

LIMIT_SUBMISSIONS_ENV = "DOCDOC_LIMIT_SUBMISSIONS_PER_MINUTE"
LIMIT_CONCURRENT_ENV = "DOCDOC_LIMIT_CONCURRENT_RUNS"
LIMIT_RUNS_PER_PERIOD_ENV = "DOCDOC_LIMIT_RUNS_PER_PERIOD"
LIMIT_TOKENS_ENV = "DOCDOC_LIMIT_TOKENS_PER_PERIOD"

RUN_CREDENTIAL_TTL_ENV = _identity.CREDENTIAL_TTL_ENV
RUN_RETENTION_DAYS_ENV = _identity.RETENTION_DAYS_ENV
RUN_STARVATION_SECONDS_ENV = _identity.STARVATION_SECONDS_ENV
RUN_SWEEP_BATCH_ENV = _identity.SWEEP_BATCH_ENV

#: Webhook delivery (ADR-0018), re-exported on the same terms: `identity` owns
#: the defaults and the precedence because the backoff schedule is arithmetic on
#: an instant, and that module is the only one in `docdoc.runs` permitted to
#: import `datetime` (FR-096a).
#:
#: Every one of them is inert until a callback is registered, and a deployment
#: that registers none performs no outbound request at all (FR-064).
DELIVERY_ATTEMPTS_ENV = _identity.DELIVERY_ATTEMPTS_ENV
DELIVERY_TIMEOUT_ENV = _identity.DELIVERY_TIMEOUT_ENV
DELIVERY_BACKOFF_ENV = _identity.DELIVERY_BACKOFF_ENV
DELIVERY_BATCH_ENV = _identity.DELIVERY_BATCH_ENV
DELIVERY_ALLOW_PRIVATE_ENV = _identity.DELIVERY_ALLOW_PRIVATE_ENV
DELIVERY_SECRETS_FILE_ENV = _identity.DELIVERY_SECRETS_FILE_ENV

#: Where spans go, and what to authenticate to the collector with. **Unset means
#: nothing is exported and no telemetry dependency is required** (FR-018), which
#: is why the base install acquires neither `opentelemetry-sdk` nor the exporter
#: (SC-021). The headers are `k=v,k=v`, the form every OTLP tool already uses.
#:
#: Imported from the layer that reads them rather than restated, exactly as the
#: lease pair is: `docdoc.telemetry` sits below both front ends, which makes it
#: the one place a single copy can live.
OTLP_ENDPOINT_ENV = _telemetry.ENDPOINT_ENV
OTLP_HEADERS_ENV = _telemetry.HEADERS_ENV

#: The highest priority a tenant may be granted (FR-087b). Per tenant through the
#: same file the limits use, with a deployment-wide default here.
#:
#: **Reachable through no route.** A tenant that could raise its own ceiling has
#: self-service escalation past another tenant's queue, which is the whole reason
#: the ceiling exists rather than letting a client choose freely.
PRIORITY_CEILING_ENV = "DOCDOC_PRIORITY_CEILING"

#: The routing policy, as a path to a JSON document. **Unset means no policy**,
#: so `GET /v1/runs/{id}` carries no `routing` block at all rather than one
#: saying nothing (FR-075).
ROUTING_POLICY_ENV = "DOCDOC_ROUTING_POLICY"

#: How long a correction is kept, independently of its run (FR-013).
CORRECTION_RETENTION_DAYS_ENV = _identity.CORRECTION_RETENTION_DAYS_ENV

#: How often a worker ticks, and how long one tick may take. Read by the worker
#: and named here for the reason the lease pair is: they are part of "how run
#: state behaves", and splitting a deployment's run configuration across two
#: modules by which process happens to read each is how a setting ends up
#: documented in neither.
MAINTENANCE_INTERVAL_ENV = _identity.MAINTENANCE_INTERVAL_ENV
MAINTENANCE_BUDGET_ENV = _identity.MAINTENANCE_BUDGET_ENV

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
