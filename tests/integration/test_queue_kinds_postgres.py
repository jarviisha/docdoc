"""`tests/unit/test_queue_kinds.py`'s cases against a real database (014)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from tests.infra import require_database
from tests.unit.test_queue_kinds import CASES, NOW, Spec, ocr, submit

from docdoc.runs import migrations
from docdoc.runs.limits import LimitPolicy, PostgresLimiter
from docdoc.runs.postgres import PostgresRunQueue

pytestmark = pytest.mark.postgres


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.__name__)
def test_case(queue: PostgresRunQueue, case: Callable[[Any], None]) -> None:
    case(queue)


def test_concurrent_runs_ignores_ocr(queue: PostgresRunQueue) -> None:
    """An OCR backlog never blocks a tenant's extraction submissions."""
    submit(queue, ocr("a"))
    limiter = PostgresLimiter(execute=queue._execute, policy=LimitPolicy(concurrent_runs=1))
    assert limiter.check(tenant_id="a", now=NOW).allowed
    submit(queue, Spec(tenant_id="a"))
    assert not limiter.check(tenant_id="a", now=NOW).allowed
