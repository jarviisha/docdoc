"""Reading Milestone 4's verdict about evidence, and never re-deciding it.

This module answers one question per present value: *was it located, and does
this run care?* It reads the recorded ``GroundingStatus`` and never recomputes,
upgrades, or downgrades it (FR-006). Two stages that both decide where a value is
would eventually disagree, and the one holding no document would be the one that
was wrong.

A value the model reported absent produces no check at all. That mirrors
Milestone 4's rule about the grounding rate: a correctly reported absence is not
a failure to locate anything, and counting it as one would let an honest model
depress a quality signal (FR-036).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docdoc.grounding.result import GroundingOutcome, GroundingStatus
from docdoc.validation import record
from docdoc.validation.result import CheckKind, ReasonCode

if TYPE_CHECKING:
    from docdoc.validation.enumerate import Slot
    from docdoc.validation.options import GroundingPolicy
    from docdoc.validation.record import CheckRecord
    from docdoc.validation.severity import Severity

__all__ = ["check_grounding"]

_REASONS = {
    GroundingStatus.UNGROUNDED: ReasonCode.VALUE_NOT_GROUNDED,
    GroundingStatus.FUZZY: ReasonCode.VALUE_GROUNDED_APPROXIMATELY,
    GroundingStatus.EXACT: ReasonCode.VALUE_GROUNDED_APPROXIMATELY,
}


def check_grounding(
    slot: Slot,
    outcome: GroundingOutcome | None,
    policy: GroundingPolicy,
) -> CheckRecord | None:
    """One grounding check for one present value, or ``None`` where none applies."""
    if slot.field.is_grouping or not slot.present:
        return None
    status = outcome.status if outcome is not None else GroundingStatus.UNGROUNDED
    severity = _severity_for(status, policy)
    if severity is None:
        return None

    check_id = f"{slot.path}#grounding"
    if status is GroundingStatus.EXACT:
        # Reported only where a deployment asked to see its exact-tier coverage,
        # and then as a passing check rather than a finding: an exactly located
        # value has nothing wrong with it.
        return record.passed(check_id, slot.path, CheckKind.GROUNDING)

    score = outcome.score if outcome is not None else None
    return record.failed(
        check_id,
        slot.path,
        CheckKind.GROUNDING,
        reason=_REASONS[status],
        severity=severity,
        expected="a located value",
        # The score is carried, and deliberately never compared with an exact
        # tier's: an exact score is 1.0 by definition while a fuzzy one is a
        # measurement, so ranking across them is meaningless (ADR-0004, FR-037).
        actual=(
            "ungrounded"
            if status is GroundingStatus.UNGROUNDED
            else f"approximate, score {score:.4f}"
            if score is not None
            else "approximate"
        ),
        message=(
            f"{slot.path!r} is present but nothing in the document was found to support it"
            if status is GroundingStatus.UNGROUNDED
            else f"{slot.path!r} was located approximately rather than verbatim"
        ),
    )


def _severity_for(status: GroundingStatus, policy: GroundingPolicy) -> Severity | None:
    if status is GroundingStatus.UNGROUNDED:
        return policy.ungrounded
    if status is GroundingStatus.FUZZY:
        return policy.fuzzy
    return policy.exact
