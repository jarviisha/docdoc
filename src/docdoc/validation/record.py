"""The internal shape every check produces before it becomes public output.

One record carries what both public views need: ``CheckOutcome`` is its
"did this run?" projection and ``Finding`` is its "what was wrong?" projection.
They are derived from the same record so the two cannot disagree -- which is the
whole reason `checks` and `findings` are not assembled independently.

Not exported from the package. A caller reads the two public types.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from docdoc.validation.result import CheckKind, Outcome, ReasonCode
from docdoc.validation.severity import Severity

__all__ = ["CheckRecord", "failed", "not_evaluated", "passed"]


@dataclass(frozen=True, slots=True)
class CheckRecord:
    check_id: str
    field_path: str
    kind: CheckKind
    outcome: Outcome
    reason: ReasonCode | None = None

    #: Resolved when the record is built, because the producer is the only thing
    #: that knows whether an author overrode it. ``None`` for a passing check,
    #: which has no severity to have.
    severity: Severity | None = None

    expected: str | None = None
    actual: str | None = None
    participants: tuple[str, ...] = field(default_factory=tuple)
    message: str = ""

    #: Set by the rule engine so a finding can name its rule without anyone
    #: parsing ``check_id`` (FR-028, FR-039).
    rule_id: str | None = None


def passed(check_id: str, field_path: str, kind: CheckKind) -> CheckRecord:
    return CheckRecord(check_id=check_id, field_path=field_path, kind=kind, outcome=Outcome.PASSED)


def failed(
    check_id: str,
    field_path: str,
    kind: CheckKind,
    *,
    reason: ReasonCode,
    severity: Severity,
    expected: str | None = None,
    actual: str | None = None,
    participants: tuple[str, ...] = (),
    message: str = "",
    rule_id: str | None = None,
) -> CheckRecord:
    return CheckRecord(
        check_id=check_id,
        field_path=field_path,
        kind=kind,
        outcome=Outcome.FAILED,
        reason=reason,
        severity=severity,
        expected=expected,
        actual=actual,
        participants=participants or (field_path,),
        message=message,
        rule_id=rule_id,
    )


def not_evaluated(
    check_id: str,
    field_path: str,
    kind: CheckKind,
    *,
    reason: ReasonCode,
    participants: tuple[str, ...] = (),
    message: str = "",
    rule_id: str | None = None,
) -> CheckRecord:
    """A check that could not run.

    Severity is always ``WARNING`` and deliberately carries no verdict weight:
    the verdict takes ``incomplete`` from the *outcome*, not from this. Every
    record having a severity keeps the shape uniform for a consumer filtering by
    it; letting this one decide the verdict would mean a document could be
    rejected for a reason that is about the result rather than about the document
    (VAL-11, FR-041).
    """
    return CheckRecord(
        check_id=check_id,
        field_path=field_path,
        kind=kind,
        outcome=Outcome.NOT_EVALUATED,
        reason=reason,
        severity=Severity.WARNING,
        participants=participants or (field_path,),
        message=message,
        rule_id=rule_id,
    )
