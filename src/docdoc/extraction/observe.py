"""One structured event per extraction, and nothing that could leak content.

The rule is absolute and it is why this module exists rather than each call site
logging what seemed useful: document text, extracted values, claimed source text,
prompt content, and credentials **never** reach a log. Identifiers, hashes,
counts, timings, and token counts only (FR-039).

Emitted on every path, success and failure alike. An event only on success would
make "why did this document fail?" answerable only by re-running the extraction,
which for a paid model call is the wrong answer.
"""

from __future__ import annotations

import logging
from typing import Any

__all__ = ["EVENT_NAME", "emit_extraction_event"]

EVENT_NAME = "extraction.extract"

_logger = logging.getLogger("docdoc.extraction")

#: Every key an event may carry. A closed set, so adding a field to the event is
#: a visible change here rather than a keyword argument appearing at one call
#: site -- which is how a content field eventually gets logged by accident.
_ALLOWED_KEYS = frozenset(
    {
        "adapter_id",
        "adapter_version",
        "artifact_id",
        "attempts",
        "document_id",
        "duration_ms",
        "estimated_input_tokens",
        "extractor_version",
        "seed",
        "temperature",
        "input_tokens",
        "model_id",
        "model_version",
        "outcome",
        "output_tokens",
        "cache_read_input_tokens",
        "prompt_hash",
        "reason",
        "schema_hash",
        "schema_identity",
        "values_absent",
        "values_present",
    }
)


def emit_extraction_event(**fields: Any) -> dict[str, Any]:
    """Log one event and return it, so a test can assert on the same object.

    Returning the payload is deliberate: a test that reconstructs what it thinks
    was logged is testing its own reconstruction.
    """
    unknown = sorted(set(fields) - _ALLOWED_KEYS)
    if unknown:
        raise ValueError(
            f"extraction event carries unknown field(s) {unknown}. The key set is closed so "
            "that a content-bearing field cannot be added at a call site"
        )
    event = {"event": EVENT_NAME, **{k: v for k, v in fields.items() if v is not None}}
    _logger.info(EVENT_NAME, extra={"docdoc": event})
    return event
