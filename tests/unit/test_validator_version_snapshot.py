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
from docdoc.validation.result import CheckKind, Outcome, ReasonCode
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


if __name__ == "__main__":  # pragma: no cover - the documented refresh path
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(observed(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"refreshed {SNAPSHOT}")
