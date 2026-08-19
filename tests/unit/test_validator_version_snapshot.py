"""T069, T052 — the change detectors for the two versions this stage owns.

Modelled on `tests/unit/test_schema_snapshot.py`, and for the same reason ADR-0008
gives there: no system can decide on its own whether a change was semantic. What
a check like this can guarantee is that the judgment is **made** rather than
skipped.

Both snapshots are deliberately about *behaviour*, not about source text. They
pin what a consumer could observe — the default severities, how the verdict is
derived, the shape of a check id, the reason vocabulary, and what each rule kind
computes — so a refactor that changes none of those passes untouched, and a
one-character change to a default trips it.
"""

from __future__ import annotations

import json
import pathlib

from docdoc.extraction.schema import RuleKind
from docdoc.validation import Severity, Verdict
from docdoc.validation.identity import VALIDATOR_ID, VALIDATOR_VERSION
from docdoc.validation.options import GroundingPolicy
from docdoc.validation.record import failed, not_evaluated, passed
from docdoc.validation.result import CheckKind, CheckOutcome, Finding, Outcome, ReasonCode
from docdoc.validation.rules import RULE_VOCABULARY_VERSION
from docdoc.validation.verdict import derive_verdict

SNAPSHOT = pathlib.Path("tests/fixtures/snapshots/validator_behaviour.json")

_REMEDY = """
The validator's observable behaviour has moved. That is either:

  (a) a real change to what a verdict MEANS -- a default severity, the verdict
      derivation, a check id format, a reason code, or what a rule kind computes.
      Bump VALIDATOR_VERSION (and RULE_VOCABULARY_VERSION if the vocabulary
      changed), then refresh this snapshot and say which in the commit message.
      A stored verdict must stay explainable, and it can only be explained
      against the version that produced it (FR-050, VAL-2).

  (b) an accident. Then this check just caught it.

Refresh with:

    uv run python tests/unit/test_validator_version_snapshot.py

Do not clear this by editing the assertions.
"""


def _verdict_truth_table() -> dict[str, str]:
    """Every combination of outcomes that can occur, and the verdict it produces."""

    def _p():
        return passed("x#required", "x", CheckKind.REQUIRED)

    def _f(severity: Severity):
        return failed(
            "x#pattern",
            "x",
            CheckKind.CONSTRAINT,
            reason=ReasonCode.PATTERN_UNMATCHED,
            severity=severity,
        )

    def _n():
        return not_evaluated("rule:x@x", "x", CheckKind.RULE, reason=ReasonCode.OPERAND_ABSENT)

    cases = {
        "nothing": (),
        "passed": (_p(),),
        "error": (_f(Severity.ERROR),),
        "warning": (_f(Severity.WARNING),),
        "info": (_f(Severity.INFO),),
        "not_evaluated": (_n(),),
        "error+not_evaluated": (_f(Severity.ERROR), _n()),
        "warning+not_evaluated": (_f(Severity.WARNING), _n()),
        "passed+warning": (_p(), _f(Severity.WARNING)),
    }
    return {name: str(derive_verdict(records)) for name, records in cases.items()}


def _finding_order() -> list[str]:
    """The ordering rule, as something a snapshot can hold.

    FR-050 names the finding order alongside the default severities and the
    verdict derivation, and convergence found it was the one of the four the
    snapshot did not pin — so changing the sort key failed no build. Recorded as
    the *result* of sorting a fixed set that spans two fields, two entry indices,
    and three check kinds, because that is what a consumer observes.
    """
    from docdoc.extraction.schema import Cardinality, FieldSpec, FieldType, Schema
    from docdoc.extraction.value import ExtractedValue
    from docdoc.validation.enumerate import walk
    from docdoc.validation.verdict import sort_key

    def value(path: str, payload: object) -> ExtractedValue:
        return ExtractedValue(field_path=path, value=payload, present=True)

    schema = Schema(
        name="order_probe",
        version=1,
        fields=(
            FieldSpec(name="alpha", type=FieldType.STRING, required=True),
            FieldSpec(name="beta", type=FieldType.STRING, required=True),
            FieldSpec(
                name="lines",
                cardinality=Cardinality.REPEATING_GROUP,
                fields=(FieldSpec(name="amount", type=FieldType.DECIMAL),),
            ),
        ),
    )
    values = {
        "alpha": value("alpha", "a"),
        "beta": value("beta", "b"),
        "lines": tuple({"amount": value(f"lines[{index}].amount", 1)} for index in range(2)),
    }
    index = walk(schema, values)
    records = [
        passed("beta#required", "beta", CheckKind.REQUIRED),
        passed("alpha#grounding", "alpha", CheckKind.GROUNDING),
        passed("alpha#required", "alpha", CheckKind.REQUIRED),
        passed("rule:r@lines[1].amount", "lines[1].amount", CheckKind.RULE),
        passed("rule:r@lines[0].amount", "lines[0].amount", CheckKind.RULE),
        passed("lines#min_length", "lines", CheckKind.CONSTRAINT),
    ]
    ordered = sorted(records, key=lambda item: sort_key(item, index))
    return [item.check_id for item in ordered]


def _rule_semantics() -> dict[str, list[str]]:
    """What each rule kind *computes*, as something a snapshot can hold.

    VAL-2 says `RULE_VOCABULARY_VERSION` designates the member set **and each
    kind's semantics**. Until a convergence pass went looking, the snapshot held
    only the four names: changing what `sum_equals` computes would break
    `tests/unit/test_rules.py`, a contributor would update that test to match, and
    the change would ship with the version untouched — which is exactly the
    review-discipline failure a snapshot exists to convert into a build failure.

    Semantics are recorded as **observed outcomes**, not as source text. Each
    fixture is chosen so its kind's answer is distinctive: a sum short by a known
    amount, a product off by one factor, a comparison false in one direction only,
    a conditional whose antecedent holds and whose companion is absent. A change
    to any kind's arithmetic, anchor, or tolerance convention moves these lines.
    """
    from decimal import Decimal

    from docdoc.extraction.schema import (
        Cardinality,
        FieldSpec,
        FieldType,
        Operator,
        RuleKind,
        RuleSpec,
        Schema,
    )
    from docdoc.extraction.value import ExtractedValue
    from docdoc.validation.enumerate import walk
    from docdoc.validation.rules import check_rules

    def value(path: str, payload: object, *, present: bool = True) -> ExtractedValue:
        return ExtractedValue(field_path=path, value=payload, present=present)

    schema = Schema(
        name="semantics_probe",
        version=1,
        rules=(
            RuleSpec(
                id="sum_rule",
                kind=RuleKind.SUM_EQUALS,
                operands=("lines.amount", "total"),
            ),
            RuleSpec(
                id="product_rule",
                kind=RuleKind.PRODUCT_EQUALS,
                operands=("lines.quantity", "lines.unit_price", "lines.amount"),
            ),
            RuleSpec(
                id="comparison_rule",
                kind=RuleKind.COMPARISON,
                operands=("total", "subtotal"),
                operator=Operator.LE,
            ),
            RuleSpec(
                id="presence_rule",
                kind=RuleKind.CONDITIONAL_PRESENCE,
                operands=("total", "reference"),
            ),
            # The author's override — the branch of the severity logic no other
            # fixture reaches. Without it, deleting `_severity()`'s override path
            # left the whole suite green, which is how convergence found it. The
            # rule is deliberately a duplicate of `sum_rule` in everything but its
            # severity, so this line moves if and only if the override resolves
            # differently (FR-040, VAL-11).
            RuleSpec(
                id="overridden_rule",
                kind=RuleKind.SUM_EQUALS,
                operands=("lines.amount", "total"),
                severity="warning",
            ),
        ),
        fields=(
            FieldSpec(name="total", type=FieldType.DECIMAL),
            FieldSpec(name="subtotal", type=FieldType.DECIMAL),
            FieldSpec(name="reference", type=FieldType.STRING),
            FieldSpec(
                name="lines",
                cardinality=Cardinality.REPEATING_GROUP,
                fields=(
                    FieldSpec(name="quantity", type=FieldType.INTEGER),
                    FieldSpec(name="unit_price", type=FieldType.DECIMAL),
                    FieldSpec(name="amount", type=FieldType.DECIMAL),
                ),
            ),
        ),
    )
    values = {
        # 100.00 short of the two lines: distinctive, so a sum over the wrong
        # operand or a different anchor changes the recorded numbers.
        "total": value("total", Decimal("200.00")),
        # Below the total, so `total <= subtotal` is false in exactly one
        # direction — flipping the comparison flips this line.
        "subtotal": value("subtotal", Decimal("150.00")),
        # Absent, so the conditional fires and fails rather than passing vacuously.
        "reference": value("reference", None, present=False),
        "lines": (
            {
                "quantity": value("lines[0].quantity", 2),
                "unit_price": value("lines[0].unit_price", Decimal("50.00")),
                # 100.00, so the product is right on this entry...
                "amount": value("lines[0].amount", Decimal("100.00")),
            },
            {
                "quantity": value("lines[1].quantity", 4),
                "unit_price": value("lines[1].unit_price", Decimal("50.00")),
                # ...and wrong on this one: 4 x 50.00 is 200.00, not 150.00. The
                # first draft of this fixture stated 200.00 and therefore passed,
                # which the snapshot caught on its first run — a reminder that a
                # fixture meant to be wrong has to be checked as carefully as the
                # code it pins.
                "amount": value("lines[1].amount", Decimal("150.00")),
            },
        ),
    }
    index = walk(schema, values)
    records = check_rules(schema, index, enabled=None)
    return {
        "outcomes": [
            "|".join(
                (
                    item.check_id,
                    str(item.outcome),
                    # Severity is part of what a kind *produces*, not decoration:
                    # `overridden_rule` differs from `sum_rule` in nothing else, so
                    # without this column the two lines were identical and the
                    # snapshot could not see an override resolving differently.
                    str(item.severity) if item.severity else "-",
                    str(item.reason) if item.reason else "-",
                    item.expected or "-",
                    item.actual or "-",
                )
            )
            for item in records
        ],
        "participants": [f"{item.check_id}: {','.join(item.participants)}" for item in records],
    }


def observed() -> dict[str, object]:
    policy = GroundingPolicy()
    return {
        "validator_id": VALIDATOR_ID,
        "validator_version": VALIDATOR_VERSION,
        "rule_vocabulary_version": RULE_VOCABULARY_VERSION,
        "rule_kinds": sorted(str(kind) for kind in RuleKind),
        "reason_codes": sorted(str(code) for code in ReasonCode),
        "check_kinds": sorted(str(kind) for kind in CheckKind),
        "outcomes": sorted(str(item) for item in Outcome),
        "verdicts": sorted(str(item) for item in Verdict),
        "severities": sorted(str(item) for item in Severity),
        "default_grounding_policy": {
            "ungrounded": str(policy.ungrounded) if policy.ungrounded else None,
            "fuzzy": str(policy.fuzzy) if policy.fuzzy else None,
            "exact": str(policy.exact) if policy.exact else None,
        },
        "default_severities": {
            "required": str(Severity.ERROR),
            "constraint": str(Severity.ERROR),
            "rule": str(Severity.ERROR),
            "not_evaluated": str(
                not_evaluated("a", "a", CheckKind.RULE, reason=ReasonCode.OPERAND_ABSENT).severity
            ),
        },
        "check_id_formats": [
            "<field_path>#required",
            "<field_path>#<constraint_key>",
            "<field_path>#grounding",
            "rule:<rule_id>@<anchor_path>",
        ],
        "verdict_truth_table": _verdict_truth_table(),
        "finding_order": _finding_order(),
        # What each kind *computes*, not only what it is called (VAL-2).
        "rule_semantics": _rule_semantics(),
        # The *shape* of what a consumer reads. Adding or removing a field changes
        # the output for unchanged inputs, which FR-050 says moves the version --
        # and `Finding.rule_id` is the case that taught this snapshot to hold it.
        "finding_fields": sorted(Finding.model_fields),
        "check_outcome_fields": sorted(CheckOutcome.model_fields),
    }


def test_the_observable_behaviour_has_not_moved() -> None:
    assert SNAPSHOT.exists(), f"snapshot missing; create it with:\n{_REMEDY}"
    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert observed() == recorded, _REMEDY


def test_the_snapshot_is_not_vacuous() -> None:
    """A change detector that checks three constants detects three changes."""
    recorded = observed()
    assert len(recorded["reason_codes"]) >= 15
    assert len(recorded["verdict_truth_table"]) >= 8
    assert len(recorded["finding_order"]) >= 6
    assert "rule_id" in recorded["finding_fields"]
    semantics = recorded["rule_semantics"]
    assert len(semantics["outcomes"]) >= 5, "every kind must contribute an observed outcome"
    assert any("passed" in line for line in semantics["outcomes"])
    assert any("failed" in line for line in semantics["outcomes"])


if __name__ == "__main__":  # pragma: no cover - the documented refresh path
    import sys

    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(observed(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"refreshed {SNAPSHOT}")
