"""The four rule kinds, evaluated by one generic engine.

Nothing in this module knows what an invoice is. It reads a `RuleSpec` -- schema
data -- resolves its operand paths against the value tree, and applies the
arithmetic its kind names. That is Principle VI enforced by construction: a
per-document-type validator would be the ``InvoiceService`` the constitution
forbids, and a rule expressed as a prompt instruction would be the Principle VII
violation this whole milestone exists to prevent.

**An absent operand is never zero.** A sum rule whose line is missing an amount
reports `not_evaluated` naming that line, because summing a missing amount as
zero is precisely how a wrong total passes. An *empty* repeating group is
different and is evaluated: the sum of no entries is a defined quantity, and a
document claiming a total over no lines is exactly the case a reader wants
flagged (FR-031).
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

from docdoc.extraction.schema import Operator, RuleKind, RuleSpec, Schema
from docdoc.validation import record
from docdoc.validation.numeric import as_decimal, render, within_tolerance
from docdoc.validation.result import CheckKind, ReasonCode
from docdoc.validation.severity import Severity

if TYPE_CHECKING:
    from docdoc.extraction.value import ExtractedValue
    from docdoc.validation.enumerate import ValueIndex
    from docdoc.validation.record import CheckRecord

__all__ = ["RULE_VOCABULARY_VERSION", "check_rules"]

#: Designates the **whole** vocabulary: which kinds exist, what each one
#: computes, the tolerance convention, and the anchor each check is addressed to.
#: Adding, removing, or altering any of them REQUIRES a bump, which
#: ``tests/unit/test_rule_vocabulary_snapshot.py`` turns into a build failure
#: rather than a review obligation (VAL-2, FR-027).
RULE_VOCABULARY_VERSION = "rule_vocabulary@1"

_COMPARATORS = {
    Operator.EQ: lambda a, b: a == b,
    Operator.NE: lambda a, b: a != b,
    Operator.LT: lambda a, b: a < b,
    Operator.LE: lambda a, b: a <= b,
    Operator.GT: lambda a, b: a > b,
    Operator.GE: lambda a, b: a >= b,
}


def check_rules(
    schema: Schema, index: ValueIndex, *, enabled: frozenset[str] | None
) -> list[CheckRecord]:
    """Every enabled rule, at every place it applies, in declaration order."""
    checks: list[CheckRecord] = []
    for rule in schema.rules:
        if enabled is not None and rule.id not in enabled:
            continue
        checks.extend(_rule(schema, rule, index))
    return checks


def _rule(schema: Schema, rule: RuleSpec, index: ValueIndex) -> list[CheckRecord]:
    if rule.kind is RuleKind.SUM_EQUALS:
        return [_sum_equals(schema, rule, index)]
    scope = schema.repeating_ancestor(rule.operands[0])
    if scope is None:
        return [_at(rule, index, entry=None)]
    entries = index.entry_counts.get(scope, 0)
    return [_at(rule, index, entry=(scope, position)) for position in range(entries)]


def _anchor(rule: RuleSpec, entry: tuple[str, int] | None) -> tuple[str, tuple[str, ...]]:
    """The path a check is addressed to, and every path it reads (FR-032)."""
    participants = tuple(_resolve(operand, entry) for operand in rule.operands)
    return participants[0], participants


def _resolve(operand: str, entry: tuple[str, int] | None) -> str:
    """Turn a declared path into an indexed one, where the rule runs per entry."""
    if entry is None:
        return operand
    group, position = entry
    if operand.startswith(f"{group}."):
        return f"{group}[{position}].{operand[len(group) + 1 :]}"
    return operand


def _check_id(rule: RuleSpec, anchor: str) -> str:
    return f"rule:{rule.id}@{anchor}"


def _severity(rule: RuleSpec) -> Severity:
    """The author's override, or the documented default (FR-040)."""
    return Severity(rule.severity) if rule.severity is not None else Severity.ERROR


def _value(index: ValueIndex, path: str) -> ExtractedValue | None:
    return index.values.get(path)


def _operand(index: ValueIndex, path: str) -> tuple[Decimal | None, ReasonCode | None]:
    """One numeric operand, or the reason it could not be read."""
    found = _value(index, path)
    if found is None:
        return None, ReasonCode.OPERAND_GROUP_ABSENT
    if not found.present:
        return None, ReasonCode.OPERAND_ABSENT
    amount = as_decimal(found.value)
    if amount is None:
        return None, ReasonCode.TYPE_MISMATCH
    return amount, None


def _sum_equals(schema: Schema, rule: RuleSpec, index: ValueIndex) -> CheckRecord:
    member, total_path = rule.operands
    group = schema.repeating_ancestor(member)
    assert group is not None  # guaranteed by VAL-5 at load
    entries = index.entry_counts.get(group, 0)
    member_paths = tuple(_resolve(member, (group, position)) for position in range(entries))
    participants = (total_path, *member_paths)
    check_id = _check_id(rule, total_path)

    total, missing = _operand(index, total_path)
    if total is None:
        return record.not_evaluated(
            check_id,
            total_path,
            CheckKind.RULE,
            reason=missing or ReasonCode.OPERAND_ABSENT,
            participants=participants,
            rule_id=rule.id,
            message=f"rule {rule.id!r} cannot run: {total_path!r} has no value",
        )

    running = Decimal(0)
    for path in member_paths:
        amount, missing = _operand(index, path)
        if amount is None:
            return record.not_evaluated(
                check_id,
                total_path,
                CheckKind.RULE,
                reason=missing or ReasonCode.OPERAND_ABSENT,
                participants=participants,
                message=(
                    f"rule {rule.id!r} cannot run: {path!r} has no value, and a missing "
                    "amount is not zero"
                ),
            )
        running += amount

    if within_tolerance(running, total, rule.tolerance):
        return record.passed(check_id, total_path, CheckKind.RULE)
    return record.failed(
        check_id,
        total_path,
        CheckKind.RULE,
        reason=ReasonCode.SUM_MISMATCH,
        severity=_severity(rule),
        rule_id=rule.id,
        expected=render(running),
        actual=render(total),
        participants=participants,
        message=(
            f"rule {rule.id!r}: {total_path!r} is {render(total)}, the {entries} "
            f"line(s) sum to {render(running)}, difference {render(abs(running - total))}"
        ),
    )


def _at(rule: RuleSpec, index: ValueIndex, *, entry: tuple[str, int] | None) -> CheckRecord:
    anchor, participants = _anchor(rule, entry)
    check_id = _check_id(rule, anchor)
    if rule.kind is RuleKind.PRODUCT_EQUALS:
        return _product_equals(rule, index, anchor, participants, check_id)
    if rule.kind is RuleKind.COMPARISON:
        return _comparison(rule, index, anchor, participants, check_id)
    return _conditional_presence(rule, index, anchor, participants, check_id)


def _product_equals(
    rule: RuleSpec,
    index: ValueIndex,
    anchor: str,
    participants: tuple[str, ...],
    check_id: str,
) -> CheckRecord:
    left_path, right_path, product_path = participants
    factors: list[Decimal] = []
    for path in (left_path, right_path, product_path):
        amount, missing = _operand(index, path)
        if amount is None:
            return record.not_evaluated(
                check_id,
                anchor,
                CheckKind.RULE,
                reason=missing or ReasonCode.OPERAND_ABSENT,
                participants=participants,
                message=f"rule {rule.id!r} cannot run: {path!r} has no value",
            )
        factors.append(amount)
    left, right, stated = factors
    computed = left * right
    if within_tolerance(computed, stated, rule.tolerance):
        return record.passed(check_id, anchor, CheckKind.RULE)
    return record.failed(
        check_id,
        anchor,
        CheckKind.RULE,
        reason=ReasonCode.PRODUCT_MISMATCH,
        severity=_severity(rule),
        rule_id=rule.id,
        expected=render(computed),
        actual=render(stated),
        participants=participants,
        message=(
            f"rule {rule.id!r}: {product_path!r} is {render(stated)}, "
            f"{render(left)} x {render(right)} is {render(computed)}"
        ),
    )


def _comparison(
    rule: RuleSpec,
    index: ValueIndex,
    anchor: str,
    participants: tuple[str, ...],
    check_id: str,
) -> CheckRecord:
    left_path, right_path = participants
    values: list[Any] = []
    for path in (left_path, right_path):
        found = _value(index, path)
        if found is None:
            return record.not_evaluated(
                check_id,
                anchor,
                CheckKind.RULE,
                reason=ReasonCode.OPERAND_GROUP_ABSENT,
                participants=participants,
                rule_id=rule.id,
                message=f"rule {rule.id!r} cannot run: {path!r} is not in this result",
            )
        if not found.present:
            return record.not_evaluated(
                check_id,
                anchor,
                CheckKind.RULE,
                reason=ReasonCode.OPERAND_ABSENT,
                participants=participants,
                rule_id=rule.id,
                message=f"rule {rule.id!r} cannot run: {path!r} has no value",
            )
        values.append(found.value)

    left, right = values
    # Numbers compare as exact decimals; everything else compares in its own
    # declared type, which the schema layer already checked is the same on both
    # sides. Nothing is coerced across types here.
    left_decimal, right_decimal = as_decimal(left), as_decimal(right)
    if left_decimal is not None and right_decimal is not None:
        left, right = left_decimal, right_decimal
    try:
        satisfied = _COMPARATORS[rule.operator](left, right)  # type: ignore[index]
    except TypeError:
        return record.not_evaluated(
            check_id,
            anchor,
            CheckKind.RULE,
            reason=ReasonCode.TYPE_MISMATCH,
            participants=participants,
            rule_id=rule.id,
            message=f"rule {rule.id!r} cannot compare {type(left).__name__} with "
            f"{type(right).__name__}",
        )
    if satisfied:
        return record.passed(check_id, anchor, CheckKind.RULE)
    return record.failed(
        check_id,
        anchor,
        CheckKind.RULE,
        reason=ReasonCode.COMPARISON_FAILED,
        severity=_severity(rule),
        rule_id=rule.id,
        expected=f"{left_path} {rule.operator} {right_path} ({render(right)})",
        actual=render(left),
        participants=participants,
        message=(
            f"rule {rule.id!r}: {left_path!r} is {render(left)}, which is not "
            f"{rule.operator} {right_path!r} at {render(right)}"
        ),
    )


def _conditional_presence(
    rule: RuleSpec,
    index: ValueIndex,
    anchor: str,
    participants: tuple[str, ...],
    check_id: str,
) -> CheckRecord:
    trigger_path, companion_path = participants
    trigger = _value(index, trigger_path)
    companion = _value(index, companion_path)
    if trigger is None or companion is None:
        return record.not_evaluated(
            check_id,
            anchor,
            CheckKind.RULE,
            reason=ReasonCode.OPERAND_GROUP_ABSENT,
            participants=participants,
            message=f"rule {rule.id!r} cannot run: an operand is not in this result",
        )
    if not trigger.present or companion.present:
        # Either the rule does not fire, or it fires and is satisfied. Both are a
        # pass: this rule asserts an implication, and a false antecedent is not a
        # check that failed to run.
        return record.passed(check_id, anchor, CheckKind.RULE)
    return record.failed(
        check_id,
        anchor,
        CheckKind.RULE,
        reason=ReasonCode.COMPANION_MISSING,
        severity=_severity(rule),
        rule_id=rule.id,
        expected=f"a value at {companion_path}",
        actual="absent",
        participants=participants,
        message=(
            f"rule {rule.id!r}: {trigger_path!r} is present, so {companion_path!r} must be too"
        ),
    )
