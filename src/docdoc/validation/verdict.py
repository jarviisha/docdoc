"""Counts, ordering, locations, and the derivation of the verdict itself.

Everything here is mechanical on purpose. The verdict is computed from the check
outcomes by one function with no options, so there is no place for a policy
decision to hide -- FR-046 puts routing outside this stage, and a configurable
verdict would be routing wearing a different name.

The ordering is a **total** order (VAL-28): the anchor's position in the
enumeration walk, then the entry index, then the check id. Total because two
findings at the same anchor still have distinct check ids, so no output can
depend on dict order, hash seed, or platform (FR-043, SC-013).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from docdoc.validation.result import (
    CheckOutcome,
    Finding,
    Outcome,
    Severity,
    ValidationCounts,
    Verdict,
)

if TYPE_CHECKING:
    from docdoc.grounding.result import GroundingOutcome
    from docdoc.validation.enumerate import ValueIndex
    from docdoc.validation.record import CheckRecord

__all__ = ["assemble", "count", "derive_verdict", "sort_key"]

_INDEX = re.compile(r"\[(\d+)\]")


def sort_key(record: CheckRecord, index: ValueIndex) -> tuple[int, tuple[int, ...], str]:
    """Walk position, then entry indices, then check id (VAL-28).

    **When the middle term decides anything.** Every anchor the current rule kinds
    produce is a scalar path the walk already emitted, so its position orders it
    and the index is never consulted. The term is load-bearing exactly for an
    anchor the walk did **not** produce but which carries an index -- a rule kind
    anchored at ``line_items[2]`` itself rather than at a field inside it, which
    the vocabulary does not yet have. Those records all share the same fallback
    position, and without the index they would order by ``check_id`` alone, so
    entry 10 would sort before entry 2.

    Recorded rather than removed, and pinned by
    ``tests/unit/test_finding_order_is_total.py``: a convergence pass found that
    dropping the term broke no test, which left a reader unable to tell dead code
    from load-bearing code.
    """
    position = index.order.get(record.field_path)
    if position is None:
        # A path the walk did not produce sorts after everything it did, rather
        # than at position zero where a missing key would put it.
        position = len(index.order)
    indices = tuple(int(found) for found in _INDEX.findall(record.field_path))
    return position, indices, record.check_id


def derive_verdict(records: tuple[CheckRecord, ...]) -> Verdict:
    """`invalid` if anything failed at error severity, else `incomplete` if
    anything could not be evaluated, else `valid` (FR-041).

    The order of the two tests is the whole design. A run that both failed a
    check and skipped another is `invalid`, because a document that broke a rule
    is rejected whether or not the rest was fully audited. A run that skipped
    something and failed nothing is **not** `valid`: nothing failed, but nobody
    can say everything was checked.
    """
    if any(
        record.outcome is Outcome.FAILED and record.severity is Severity.ERROR for record in records
    ):
        return Verdict.INVALID
    if any(record.outcome is Outcome.NOT_EVALUATED for record in records):
        return Verdict.INCOMPLETE
    return Verdict.VALID


def count(records: tuple[CheckRecord, ...]) -> ValidationCounts:
    """Totals that reconcile by construction (VAL-27)."""
    passed = sum(1 for item in records if item.outcome is Outcome.PASSED)
    failed = sum(1 for item in records if item.outcome is Outcome.FAILED)
    skipped = sum(1 for item in records if item.outcome is Outcome.NOT_EVALUATED)
    return ValidationCounts(
        declared=len(records),
        evaluated=passed + failed,
        passed=passed,
        failed=failed,
        not_evaluated=skipped,
        errors=_severity_count(records, Severity.ERROR),
        warnings=_severity_count(records, Severity.WARNING),
        infos=_severity_count(records, Severity.INFO),
    )


def _severity_count(records: tuple[CheckRecord, ...], severity: Severity) -> int:
    return sum(
        1 for item in records if item.outcome is not Outcome.PASSED and item.severity is severity
    )


def assemble(
    records: tuple[CheckRecord, ...],
    outcomes: dict[str, GroundingOutcome],
) -> tuple[tuple[CheckOutcome, ...], tuple[Finding, ...]]:
    """Split one record list into its two public views.

    Both come from the same records, which is why ``checks`` and ``findings`` can
    never disagree about whether something failed.

    Locations are **copied** from the grounding outcome, field by field, and
    never recomputed. This stage holds no document; the only place a span could
    come from is the artifact that already computed one (FR-038, SC-011).
    """
    checks: list[CheckOutcome] = []
    findings: list[Finding] = []
    for item in records:
        checks.append(
            CheckOutcome(
                check_id=item.check_id,
                field_path=item.field_path,
                kind=item.kind,
                outcome=item.outcome,
                reason=item.reason,
            )
        )
        if item.outcome is Outcome.PASSED:
            continue
        assert item.reason is not None
        assert item.severity is not None
        located = outcomes.get(item.field_path)
        findings.append(
            Finding(
                field_path=item.field_path,
                check_id=item.check_id,
                kind=item.kind,
                reason=item.reason,
                severity=item.severity,
                expected=item.expected,
                actual=item.actual,
                participants=item.participants,
                rule_id=item.rule_id,
                span=located.span if located is not None else None,
                pages=located.pages if located is not None else (),
                geometry=located.geometry if located is not None else None,
                message=item.message,
            )
        )
    return tuple(checks), tuple(findings)
