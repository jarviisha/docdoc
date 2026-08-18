"""One structured event per grounding, and nothing that could leak content.

The rule Milestone 3 set holds here, and this layer adds a category to it:
document text, claim text, extracted values, and **match-view text** never reach
a log. The view is folded document text, so logging it -- to debug a near-miss,
say, which is exactly when someone would want to -- would leak the document just
as surely as logging ``Document.text``. Identifiers, hashes, versions, counts,
scores, and timings only (FR-046).

Emitted on every path. A refused call produces no result to log an artifact id
from, so it logs the two identities that explain the refusal, which is the whole
diagnosis for the most common failure this stage has.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docdoc.grounding.result import GroundingResult

__all__ = ["EVENT_NAME", "log_grounding", "log_refusal"]

EVENT_NAME = "grounding.ground"

_logger = logging.getLogger("docdoc.grounding")


def log_grounding(result: GroundingResult, *, duration_ms: float) -> None:
    """Emit the success event.

    ``grounding_rate`` is included because it is the metric Principle IX names
    and the reason ``counts`` exists. It is a ratio of counts, so it carries no
    content.
    """
    provenance = result.provenance
    payload: dict[str, Any] = {
        "event": EVENT_NAME,
        "outcome": "ok",
        "document_id": provenance.document_id,
        "extraction_artifact_id": provenance.extraction_artifact_id,
        "artifact_id": result.artifact_id,
        "grounding_version": provenance.grounding_version,
        "match_view_version": provenance.match_view_version,
        "view_id": provenance.view_id,
        "grounder_id": provenance.grounder_id,
        "grounder_version": provenance.grounder_version,
        "threshold": provenance.options.threshold,
        "candidate_budget": provenance.options.candidate_budget,
        "exact": result.counts.exact,
        "fuzzy": result.counts.fuzzy,
        "ungrounded": result.counts.ungrounded,
        "not_applicable": result.counts.not_applicable,
        "truncated": result.counts.truncated,
        "grounding_rate": result.counts.grounding_rate,
        "duration_ms": round(duration_ms, 3),
    }
    _logger.info(EVENT_NAME, extra={"docdoc": payload})


def log_refusal(*, document_id: str, extraction_document_id: str, duration_ms: float) -> None:
    """Emit the failure event for a document mismatch.

    Carries a duration like the success event does, even though a refusal is fast
    by construction. FR-047 asks for one event per run "successful or refused",
    and a field present on one shape and absent on the other makes the event
    harder to query than one that is always there.
    """
    _logger.warning(
        EVENT_NAME,
        extra={
            "docdoc": {
                "event": EVENT_NAME,
                "outcome": "refused",
                "reason": "document_mismatch",
                "document_id": document_id,
                "extraction_document_id": extraction_document_id,
                "duration_ms": round(duration_ms, 3),
            }
        },
    )
