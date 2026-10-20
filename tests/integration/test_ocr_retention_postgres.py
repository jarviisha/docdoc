"""`tests/unit/test_ocr_retention.py`'s sweep cases against a real database (014)."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from tests.infra import require_database
from tests.unit.test_ocr_retention import CASES

from docdoc.runs import migrations
from docdoc.runs.postgres import PostgresRunQueue

pytestmark = pytest.mark.postgres


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn, autocommit=True) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs, run_tombstones")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


@pytest.mark.parametrize("case", CASES, ids=lambda case: case.__name__)
def test_case(queue: PostgresRunQueue, case: Any, tmp_path: Path) -> None:
    case(queue, tmp_path)
