"""What a run record and a run event may contain, checked against a real run.

SC-007 and FR-093. Identifiers, hashes, states, counts, and class names — and
nothing that came out of the document.

The check is made against **strings that are actually in the document and in the
extraction**, taken from the fixture rather than invented, so it fails if the
projection ever starts copying a value through. A test that searched for a string
nothing produces would pass forever.

Two surfaces, because they leak differently. The run row is structured and would
leak by a field being added; the log line is a string and would leak by an
f-string being convenient.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from tests.infra import require_database

from docdoc.artifacts import BlobStore, FileArtifactStore
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.runs import migrations
from docdoc.runs.identity import DEFAULT_LEASE, new_run_id
from docdoc.runs.model import DEFAULT_TENANT
from docdoc.runs.postgres import PostgresRunQueue
from docdoc.runs.worker import execute_one

pytestmark = pytest.mark.postgres

FIXTURE = Path("tests/fixtures/pdf/digital_invoice.pdf")

#: Values the echo fixture returns for `invoice@1`, and text the PDF contains.
#: Read from the fixture below rather than duplicated, except for these, which
#: are the ones a leak would most plausibly carry.
DOCUMENT_TEXT = "TOTAL"


@dataclass
class Spec:
    blob_id: str
    tenant_id: str = DEFAULT_TENANT
    schema_identity: str = "invoice@1"
    request_id: str | None = None
    idempotency_key: str | None = None


@pytest.fixture
def queue() -> PostgresRunQueue:
    psycopg = pytest.importorskip("psycopg")
    dsn = require_database()
    with psycopg.connect(dsn) as connection:
        migrations.apply(connection, now=datetime.now(UTC))
        connection.execute("TRUNCATE runs")
    return PostgresRunQueue(lambda: psycopg.connect(dsn))


def _extracted_values() -> set[str]:
    """Every value and claimed text the echo fixture will produce.

    Recursive, because a schema can carry a repeating group and its rows are
    where a leak would be least noticed — a projection that copied only the top
    level would look clean while shipping every line item.
    """
    found: set[str] = set()

    def walk(node: object) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key in {"value", "claimed_text"} and isinstance(value, str) and value:
                    found.add(value)
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk(json.loads(Path("tests/fixtures/echo/invoice@1.json").read_text()))

    # Short values are dropped, and this is a limitation rather than a
    # convenience. The fixture contains line-item quantities of "1" and "2",
    # which appear inside any JSON carrying an attempt count or a hex digest —
    # substring matching cannot tell a leak from a coincidence at that length.
    # What survives is long enough to be distinctive, which is what a leak of
    # document content would actually look like.
    distinctive = {value for value in found if len(value) >= 4}
    assert distinctive, "the fixture produced no distinctive values; this would assert nothing"
    return distinctive


def _run_once(queue: PostgresRunQueue, tmp_path: Path):
    blobs = BlobStore(tmp_path)
    blob_id = blobs.put(FIXTURE.read_bytes())
    now = datetime.now(UTC)
    submitted = queue.submit(
        Spec(blob_id=blob_id),  # type: ignore[arg-type]
        run_id=new_run_id(),
        now=now,
        expires_at=now + timedelta(days=30),
    )
    claimed = queue.claim(worker_id="w1", now=now, lease=DEFAULT_LEASE, max_attempts=3)
    assert claimed is not None
    execute_one(
        claimed,
        queue=queue,
        blobs=blobs,
        store=FileArtifactStore(tmp_path),
        registry=SchemaRegistry.from_paths([Path("schemas")]),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        now=now,
    )
    return submitted.run_id


def test_the_run_record_carries_no_document_content(
    queue: PostgresRunQueue, tmp_path: Path
) -> None:
    """SC-007, over the row itself rather than over what an endpoint chose to show."""
    run_id = _run_once(queue, tmp_path)

    run = queue.get(run_id, DEFAULT_TENANT)
    assert run is not None
    serialised = json.dumps(run.model_dump(mode="json"))

    leaked = sorted(value for value in _extracted_values() if value in serialised)
    assert not leaked, (
        f"the run record carries extracted values {leaked}. It may hold "
        "identifiers, hashes, states, counts, and class names, and nothing that "
        "came out of the document (FR-037)"
    )
    assert DOCUMENT_TEXT not in serialised


def test_the_transition_event_carries_no_document_content(
    queue: PostgresRunQueue, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """FR-092 and FR-093, over the string a log line actually is.

    Also the negative half of FR-092: no duration, no token count, no cost, no
    stage result. The per-stage events state a run's cost exactly once, and a
    second statement of it would drift from the first — which is the objection
    `pipeline/observe.py` raises against a run-level summary and the reason this
    event is a transition rather than one.
    """
    with caplog.at_level(logging.INFO, logger="docdoc.runs"):
        _run_once(queue, tmp_path)

    events = [
        json.loads(record.message)
        for record in caplog.records
        if record.message.startswith("{") and '"run.transition"' in record.message
    ]
    assert events, "no run.transition event was emitted; the check would be vacuous"

    values = _extracted_values()
    for event in events:
        text = json.dumps(event)
        leaked = sorted(value for value in values if value in text)
        assert not leaked, f"a run.transition event carries {leaked}"
        assert DOCUMENT_TEXT not in text

        forbidden = {"duration_ms", "tokens", "cost", "stage_outcomes", "processing_id"}
        present = sorted(forbidden & set(event))
        assert not present, (
            f"a run.transition event carries {present}. The per-stage events "
            "already state what a run cost, and a second statement of it is the "
            "drift `pipeline/observe.py` refuses a run-level summary to avoid"
        )
