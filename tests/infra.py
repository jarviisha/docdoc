"""The two environment variables that point the suite at real infrastructure.

Milestone 9 added a database and an object store, and did not change the promise
in `CONTRIBUTING.md` that a contributor needs neither. Tests that genuinely need
one carry a marker and skip themselves when its variable is unset, which is the
same shape the live-provider tests have used since Milestone 3.

**Why these constants exist rather than a bare `os.environ` lookup in each test.**
`test_documented_api_references_resolve.py` asserts that every `DOCDOC_*` name
appearing in the documentation is defined somewhere in code, so that prose cannot
drift into describing configuration nothing reads. `CONTRIBUTING.md` documents
both names below, so both must be defined — and they belong here rather than in
`src/`, because they configure the suite and not docdoc. A shipped package should
not carry a constant naming the database its tests use.
"""

from __future__ import annotations

import os

import pytest

#: Where the `postgres`-marked tests find a database. Unset means skip.
DATABASE_URL_ENV = "DOCDOC_TEST_DATABASE_URL"

#: Where the `s3`-marked tests find an S3-compatible endpoint. Unset means skip.
S3_ENDPOINT_ENV = "DOCDOC_TEST_S3_ENDPOINT"

#: Where the `otel`-marked tests find a collector. Unset means skip — and unlike
#: the two above, the `otel` extra can also be absent, which skips them as well.
OTLP_ENDPOINT_ENV = "DOCDOC_TEST_OTLP_ENDPOINT"


def require_database() -> str:
    """The configured DSN, or skip with a reason naming what to set."""
    dsn = os.environ.get(DATABASE_URL_ENV)
    if not dsn:
        pytest.skip(
            f"no {DATABASE_URL_ENV} configured; "
            "`docker compose -f packaging/docker/compose.yml up -d postgres`"
        )
    return dsn


def require_s3_endpoint() -> str:
    """The configured endpoint, or skip with a reason naming what to set."""
    endpoint = os.environ.get(S3_ENDPOINT_ENV)
    if not endpoint:
        pytest.skip(
            f"no {S3_ENDPOINT_ENV} configured; "
            "`docker compose -f packaging/docker/compose.yml up -d minio`"
        )
    return endpoint


def require_otlp_endpoint() -> str:
    """The configured collector, or skip with a reason naming both prerequisites.

    **Two conditions, not one**, which is what makes this different from its two
    siblings: the `otel` tests need a collector *and* the `docdoc[otel]` extra.
    Either being absent is a skip, and the message says which — a contributor who
    started a collector and still saw a skip would otherwise have no way to learn
    that the extra was what was missing.

    This helper did not exist for a while and the marker it serves marked
    nothing: `pyproject.toml` registered `otel`, this module defined
    `OTLP_ENDPOINT_ENV`, and no test carried either. A marker that marks nothing
    is the same defect as a documented variable nothing reads, which this
    milestone corrected eight of in `compose.yml` — found by `/speckit-converge`
    and closed by T181.
    """
    import importlib.util

    if importlib.util.find_spec("opentelemetry") is None:
        pytest.skip("the `otel` extra is not installed; `uv sync --extra otel`")

    endpoint = os.environ.get(OTLP_ENDPOINT_ENV)
    if not endpoint:
        pytest.skip(
            f"no {OTLP_ENDPOINT_ENV} configured; point it at an OTLP/HTTP "
            "collector, e.g. http://localhost:4318/v1/traces"
        )
    return endpoint
