"""Requiredness, and the eight constraint keys Milestone 3 declared and never applied.

Every comparison here is **exact**: no case folding, no whitespace trimming, no
coercion between declared types, no locale. A value that would pass only after an
adjustment fails, because the adjustment is a silent correction and FR-004
forbids one even in the service of a kinder verdict (FR-021).

``enum`` and ``const`` are enforced here even though the extraction layer projects
them onto the wire for the provider to honour. That is not distrust of a
particular vendor; it is the same reasoning Milestone 3's ``conform`` already
applies to shape -- "the provider promised" and "the bytes that arrived" are
different claims, and only one of them is checkable (FR-020).
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from docdoc.extraction.errors import SchemaError
from docdoc.extraction.schema import Cardinality, FieldSpec
from docdoc.validation import record
from docdoc.validation.numeric import as_decimal, render, within_tolerance
from docdoc.validation.pattern import Pattern, PatternSyntaxError, compile_pattern
from docdoc.validation.result import CheckKind, ReasonCode
from docdoc.validation.severity import Severity

if TYPE_CHECKING:
    from docdoc.extraction.schema import Schema
    from docdoc.validation.enumerate import Slot
    from docdoc.validation.record import CheckRecord

__all__ = ["check_constraints", "check_required", "compile_declared_patterns"]

#: Compiled patterns, keyed by source. A schema is loaded once and validated many
#: times, so compiling per call would pay the parser's cost per value.
_PATTERN_CACHE: dict[str, Pattern] = {}


def _pattern_for(source: str) -> Pattern:
    compiled = _PATTERN_CACHE.get(source)
    if compiled is None:
        compiled = compile_pattern(source)
        _PATTERN_CACHE[source] = compiled
    return compiled


def compile_declared_patterns(schema: Schema) -> None:
    """Compile every declared ``pattern`` before any check runs (FR-056).

    **Why this is here and not in the schema layer.** FR-056 says a pattern
    outside ``pattern_dialect@1`` must be refused rather than reaching a
    validation run, and the task list asked the extraction layer's loader to do
    it. That is not implementable: `docdoc.extraction` may not import
    `docdoc.validation`, and the dialect belongs to the layer that evaluates it —
    moving the engine down would put it beneath the only layer that uses it, to
    satisfy the letter of "at load" while breaking Principle X.

    So the check runs at the *entry* to validation, which is what the requirement
    is actually about: a pattern that cannot be evaluated must never become a
    check that silently never ran. It fails before the first check is enumerated,
    naming the field and the construct.

    Raises:
        SchemaError: a declared pattern is outside the dialect.
    """
    for path in schema.field_paths():
        field = schema.field_at(path)
        if field is None:  # pragma: no cover - field_paths walks the same tree
            continue
        source = field.constraints.get("pattern")
        if source is None:
            continue
        try:
            _pattern_for(str(source))
        except PatternSyntaxError as exc:
            raise SchemaError(
                f"the pattern declared on {path!r} is not part of pattern_dialect@1: {exc}. "
                "Patterns are compiled before any check runs, so one that cannot be "
                "evaluated fails here rather than becoming a check nobody notices did "
                "not run",
                identity=schema.identity,
                field_path=path,
            ) from exc


def check_required(slot: Slot) -> CheckRecord | None:
    """One requiredness check, or ``None`` where the field is not required.

    Presence is read from what the model *reported*, not from the value's content
    (FR-015). A present empty string satisfies requiredness: Milestone 3 draws
    that distinction deliberately -- ``present=True, value=""`` means the document
    contains an empty field, ``present=False`` means it does not contain the field
    -- and erasing it here would discard information the extraction layer went out
    of its way to preserve.
    """
    if not slot.field.required:
        return None
    check_id = f"{slot.path}#required"
    if slot.field.is_grouping:
        # FR-017: one finding for the group, and the walk's own suppression keeps
        # its children from each producing a second one.
        if slot.group_absent:
            return record.failed(
                check_id,
                slot.path,
                CheckKind.REQUIRED,
                reason=ReasonCode.REQUIRED_VALUE_MISSING,
                severity=Severity.ERROR,
                expected="a value",
                actual="absent",
                message=f"required {slot.field.cardinality} {slot.path!r} is absent",
            )
        return record.passed(check_id, slot.path, CheckKind.REQUIRED)
    if slot.present:
        return record.passed(check_id, slot.path, CheckKind.REQUIRED)
    return record.failed(
        check_id,
        slot.path,
        CheckKind.REQUIRED,
        reason=ReasonCode.REQUIRED_VALUE_MISSING,
        severity=Severity.ERROR,
        expected="a value",
        actual="absent",
        message=f"required field {slot.path!r} is absent",
    )


def check_constraints(slot: Slot) -> list[CheckRecord]:
    """Every declared constraint on one field, at one place.

    Nothing is produced for a value the model reported absent: a constraint
    constrains a value, and where there is none there is no obligation. The
    requiredness check owns absence and is the one that reports it.
    """
    field = slot.field
    if not field.constraints:
        return []
    if field.cardinality is Cardinality.REPEATING_GROUP:
        return _entry_count_checks(slot)
    if not slot.present or slot.value is None:
        return []
    value = slot.value.value
    return [
        _check_one(slot, key, field.constraints[key], value) for key in sorted(field.constraints)
    ]


def _entry_count_checks(slot: Slot) -> list[CheckRecord]:
    """``min_length`` and ``max_length`` on a repeating group count entries."""
    entries = slot.entries or 0
    checks: list[CheckRecord] = []
    for key in sorted(slot.field.constraints):
        bound = int(slot.field.constraints[key])
        check_id = f"{slot.path}#{key}"
        too_few = key == "min_length" and entries < bound
        too_many = key == "max_length" and entries > bound
        if too_few or too_many:
            checks.append(
                record.failed(
                    check_id,
                    slot.path,
                    CheckKind.CONSTRAINT,
                    reason=ReasonCode.TOO_SHORT if too_few else ReasonCode.TOO_LONG,
                    severity=Severity.ERROR,
                    expected=f"{'at least' if too_few else 'at most'} {bound} entries",
                    actual=f"{entries} entries",
                    message=f"{slot.path!r} has {entries} entries",
                )
            )
        else:
            checks.append(record.passed(check_id, slot.path, CheckKind.CONSTRAINT))
    return checks


def _check_one(slot: Slot, key: str, declared: Any, value: Any) -> CheckRecord:
    check_id = f"{slot.path}#{key}"
    outcome = _EVALUATORS[key](declared, value, slot.field)
    if outcome is None:
        return record.passed(check_id, slot.path, CheckKind.CONSTRAINT)
    reason, expected, actual = outcome
    return record.failed(
        check_id,
        slot.path,
        CheckKind.CONSTRAINT,
        reason=reason,
        severity=Severity.ERROR,
        expected=expected,
        actual=actual,
        message=f"{slot.path!r} fails {key}: expected {expected}, got {actual}",
    )


#: An evaluator returns ``None`` when the value satisfies the constraint, or
#: ``(reason, expected, actual)`` when it does not.
Failure = tuple[ReasonCode, str, str] | None


def _enum(declared: Any, value: Any, field: FieldSpec) -> Failure:
    members = list(declared)
    if any(_equal(value, member, field) for member in members):
        return None
    return (
        ReasonCode.NOT_IN_ENUM,
        ", ".join(render(member) for member in members),
        render(value),
    )


def _const(declared: Any, value: Any, field: FieldSpec) -> Failure:
    if _equal(value, declared, field):
        return None
    return ReasonCode.NOT_THE_CONSTANT, render(declared), render(value)


def _equal(value: Any, declared: Any, field: FieldSpec) -> bool:
    """Exact equality, with the declared value read in the field's own type.

    A schema declares ``enum: ["1.00", "2.00"]`` in JSON as text while the value
    arrives as ``Decimal``; comparing those raw would fail for a document that is
    correct. Reading the declaration through the field's type is not coercing the
    *value* -- the value is never touched -- it is parsing the schema.
    """
    if isinstance(value, Decimal):
        parsed = as_decimal(declared) if not isinstance(declared, str) else _decimal(declared)
        return parsed is not None and parsed == value
    if isinstance(value, (date, datetime)) and isinstance(declared, str):
        return value.isoformat() == declared
    return bool(value == declared)


def _decimal(text: str) -> Decimal | None:
    try:
        return Decimal(text)
    except ArithmeticError:
        return None


def _pattern(declared: Any, value: Any, field: FieldSpec) -> Failure:
    if not isinstance(value, str):
        return ReasonCode.PATTERN_UNMATCHED, str(declared), render(value)
    if _pattern_for(str(declared)).fullmatch(value):
        return None
    return ReasonCode.PATTERN_UNMATCHED, f"a whole value matching {declared}", render(value)


def _minimum(declared: Any, value: Any, field: FieldSpec) -> Failure:
    return _bound(declared, value, low=True)


def _maximum(declared: Any, value: Any, field: FieldSpec) -> Failure:
    return _bound(declared, value, low=False)


def _bound(declared: Any, value: Any, *, low: bool) -> Failure:
    """One bound, compared in the value's own declared type.

    Dates compare as dates and numbers as exact decimals; nothing is coerced
    across the two, because the schema layer already refused a bound whose type
    could not carry it (FR-025).
    """
    limit: date | datetime | Decimal
    if isinstance(value, (date, datetime)):
        temporal = _temporal(declared, value)
        if temporal is None:
            return None
        limit = temporal
        satisfied = value >= temporal if low else value <= temporal
    else:
        left, right = as_decimal(value), as_decimal(declared) or _decimal(str(declared))
        if left is None or right is None:
            return None
        limit = right
        satisfied = left >= right if low else left <= right
    if satisfied:
        return None
    reason = ReasonCode.BELOW_MINIMUM if low else ReasonCode.ABOVE_MAXIMUM
    comparator = "at least" if low else "at most"
    return reason, f"{comparator} {render(limit)}", render(value)


def _temporal(declared: Any, value: date | datetime) -> date | datetime | None:
    if isinstance(declared, type(value)):
        return declared
    if isinstance(declared, str):
        try:
            return (
                datetime.fromisoformat(declared)
                if isinstance(value, datetime)
                else date.fromisoformat(declared)
            )
        except ValueError:
            return None
    return None


def _multiple_of(declared: Any, value: Any, field: FieldSpec) -> Failure:
    step = as_decimal(declared) or _decimal(str(declared))
    amount = as_decimal(value)
    if step is None or amount is None or step == 0:
        return None
    remainder = amount % step
    # Exact decimal remainder, and compared against zero with a zero tolerance:
    # 0.30 % 0.01 is exactly 0 here, while the same computation in binary
    # floating point is not.
    if within_tolerance(remainder, Decimal(0), Decimal(0)):
        return None
    return ReasonCode.NOT_A_MULTIPLE, f"a multiple of {render(step)}", render(value)


def _min_length(declared: Any, value: Any, field: FieldSpec) -> Failure:
    return _length(declared, value, low=True)


def _max_length(declared: Any, value: Any, field: FieldSpec) -> Failure:
    return _length(declared, value, low=False)


def _length(declared: Any, value: Any, *, low: bool) -> Failure:
    if not isinstance(value, str):
        return None
    #: Unicode **code points**, counted identically on every platform (FR-023).
    #: Not bytes, which would make a bound mean different things in different
    #: scripts, and not grapheme clusters, which would need a dependency and a
    #: version of their own. Documented wherever the bound is exposed.
    size = len(value)
    bound = int(declared)
    if (size >= bound) if low else (size <= bound):
        return None
    reason = ReasonCode.TOO_SHORT if low else ReasonCode.TOO_LONG
    comparator = "at least" if low else "at most"
    return reason, f"{comparator} {bound} characters", f"{size} characters"


_EVALUATORS = {
    "enum": _enum,
    "const": _const,
    "pattern": _pattern,
    "minimum": _minimum,
    "maximum": _maximum,
    "multiple_of": _multiple_of,
    "min_length": _min_length,
    "max_length": _max_length,
}
