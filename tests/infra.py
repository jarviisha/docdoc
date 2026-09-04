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
