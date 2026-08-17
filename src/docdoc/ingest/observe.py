"""One structured event per parse.

This is what makes "why did *this* document take the recognition path?"
answerable in a running system without re-parsing the file (FR-040).

The event carries identifiers, counts, and timings. It carries no document text,
no extracted value, no credential, and no end-user filename -- the constitution
is unambiguous that logs get hashes and identifiers only, and a log line is the
easiest place in a document pipeline to leak a customer's contract (FR-029).

Formatting is the application's choice: docdoc attaches structured fields and
emits through stdlib ``logging``. Counters, histograms, and tracing are
Milestone 7.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["EVENT_FIELDS", "log_parse", "logger"]

logger = logging.getLogger("docdoc.ingest")

#: Every field the event may carry. Named so a test can assert the schema rather
#: than eyeballing a log line.
EVENT_FIELDS = (
    "event",
    "blob_id",
    "document_id",
    "parser_id",
    "parser_version",
    "media_type",
    "text_layer_usable",
    "text_layer_rule",
    "pages",
    "duration_ms",
    "attempts",
    "outcome",
    "error_type",
    "error_reason",
)


def log_parse(
    *,
    blob_id: str,
    media_type: str,
    outcome: str,
    duration_ms: int,
    document_id: str | None = None,
    parser_id: str | None = None,
    parser_version: str | None = None,
    text_layer_usable: bool | None = None,
    text_layer_rule: str | None = None,
    pages: int | None = None,
    attempts: int = 1,
    error_type: str | None = None,
    error_reason: str | None = None,
) -> None:
    """Emit the ``ingest.parse`` event, on success and on failure alike."""
    fields: dict[str, Any] = {
        "event": "ingest.parse",
        "blob_id": blob_id,
        "document_id": document_id,
        "parser_id": parser_id,
        "parser_version": parser_version,
        "media_type": media_type,
        "text_layer_usable": text_layer_usable,
        "text_layer_rule": text_layer_rule,
        "pages": pages,
        "duration_ms": duration_ms,
        "attempts": attempts,
        "outcome": outcome,
        "error_type": error_type,
        "error_reason": error_reason,
    }
    level = logging.INFO if outcome == "ok" else logging.WARNING
    logger.log(
        level,
        "ingest.parse %s parser=%s pages=%s %dms",
        outcome,
        parser_id or "-",
        pages if pages is not None else "-",
        duration_ms,
        extra={"docdoc": fields},
    )
