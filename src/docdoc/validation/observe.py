"""One structured event per validation, and nothing that could leak content.

The rule Milestones 3 and 4 set holds here, and this stage tests it hardest:
findings carry values *by design* -- "total is 1240.00, the lines sum to 1420.00"
is what a finding is for -- while the log carries only identities, versions,
counts, and the verdict. The boundary is worth stating in the module that could
most plausibly blur it, because "values never appear anywhere" is the wrong
reading of FR-057 and would make findings useless.

Emitted on every path. A refused call has no result to log an artifact id from,
so it logs the two identities that explain the refusal -- which is the whole
diagnosis for the most common failure this stage has.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from docdoc.validation.result import ValidationResult

__all__ = ["EVENT_NAME", "log_refusal", "log_validation"]

EVENT_NAME = "validation.validate"

_logger = logging.getLogger("docdoc.validation")


def log_validation(result: ValidationResult, *, duration_ms: float) -> None:
    """Emit the success event.

    ``checks_not_evaluated`` is included next to the verdict deliberately: it is
    the number that distinguishes an audited document from one where the rules
    could not run, and a log line carrying the verdict without it would let a
    dashboard report a healthy system that had checked nothing.
    """
    provenance = result.provenance
    counts = result.counts
    payload: dict[str, Any] = {
        "event": EVENT_NAME,
        "outcome": "ok",
        "verdict": str(result.verdict),
        "document_id": provenance.document_id,
        "extraction_artifact_id": provenance.extraction_artifact_id,
        "grounding_artifact_id": provenance.grounding_artifact_id,
        "artifact_id": result.artifact_id,
        "schema_identity": provenance.schema_identity,
        "schema_hash": provenance.schema_hash,
        "rule_vocabulary_version": provenance.rule_vocabulary_version,
        "pattern_dialect_version": provenance.pattern_dialect_version,
        "validator_id": provenance.validator_id,
        "validator_version": provenance.validator_version,
        "enabled_rules": list(provenance.enabled_rules),
        "checks_declared": counts.declared,
        "checks_passed": counts.passed,
        "checks_failed": counts.failed,
        "checks_not_evaluated": counts.not_evaluated,
        "errors": counts.errors,
        "warnings": counts.warnings,
        "infos": counts.infos,
        "duration_ms": round(duration_ms, 3),
    }
    _logger.info(EVENT_NAME, extra={"docdoc": payload})


def log_refusal(
    *,
    reason: str,
    expected: str | None,
    actual: str | None,
    duration_ms: float,
) -> None:
    """Emit the refusal event.

    ``expected`` and ``actual`` are identities and hashes here -- never values --
    which is exactly what a reader needs to see that two artifacts came from
    different parses.
    """
    payload: dict[str, Any] = {
        "event": EVENT_NAME,
        "outcome": "refused",
        "reason": reason,
        "expected": expected,
        "actual": actual,
        "duration_ms": round(duration_ms, 3),
    }
    _logger.warning(EVENT_NAME, extra={"docdoc": payload})
