"""What a validation says, and the confusions its shape refuses to allow.

Three of them, each a place where a looser model would let something through:

* **`passed` and `not_evaluated` are different words.** A check that could not
  run is not a check that ran and found nothing wrong. Collapsing them is how a
  vacuous run reports success (FR-010).
* **The verdict has three states, not two.** `incomplete` exists so that
  "nothing failed" and "nothing ran" cannot share a word, and there is no boolean
  anywhere here for a caller to reach for instead (FR-041, FR-042).
* **Every check is recorded, including the ones that passed.** Six months later
  the disputed question is "did this rule run?", and a stage that keeps only its
  failures cannot answer it (FR-011).
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, model_validator

from docdoc.kernel import Geometry, Span
from docdoc.validation.options import ValidationOptions
from docdoc.validation.severity import Severity

__all__ = [
    "NOT_EVALUATED_REASONS",
    "CheckKind",
    "CheckOutcome",
    "Finding",
    "Outcome",
    "ReasonCode",
    "Severity",
    "ValidationCounts",
    "ValidationProvenance",
    "ValidationResult",
    "Verdict",
]


class Verdict(StrEnum):
    """The whole vocabulary (VAL-12).

    ``INCOMPLETE`` is the one that earns its place. Without it, a run whose rules
    could not be evaluated would report the same word as a run where every rule
    ran and passed -- and the caller would have no way to tell an audited
    document from an unaudited one.
    """

    VALID = "valid"
    INVALID = "invalid"
    INCOMPLETE = "incomplete"


class Outcome(StrEnum):
    """What became of one check. Three members, and no fourth (VAL-15)."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_EVALUATED = "not_evaluated"


class CheckKind(StrEnum):
    REQUIRED = "required"
    CONSTRAINT = "constraint"
    RULE = "rule"
    GROUNDING = "grounding"


class ReasonCode(StrEnum):
    """Why a check failed or could not run. Closed, so reasons can be counted.

    Free text cannot be aggregated, and the counts are what make a rule that
    never ran visible as a number rather than as an absence (FR-010, FR-012).
    """

    # Structural
    REQUIRED_VALUE_MISSING = "required_value_missing"

    # Constraints, one per recognised key, so a finding says which rule broke
    # rather than only that "a constraint" did.
    NOT_IN_ENUM = "not_in_enum"
    NOT_THE_CONSTANT = "not_the_constant"
    PATTERN_UNMATCHED = "pattern_unmatched"
    BELOW_MINIMUM = "below_minimum"
    ABOVE_MAXIMUM = "above_maximum"
    NOT_A_MULTIPLE = "not_a_multiple"
    TOO_SHORT = "too_short"
    TOO_LONG = "too_long"

    # Rules
    SUM_MISMATCH = "sum_mismatch"
    PRODUCT_MISMATCH = "product_mismatch"
    COMPARISON_FAILED = "comparison_failed"
    COMPANION_MISSING = "companion_missing"

    # Grounding
    VALUE_NOT_GROUNDED = "value_not_grounded"
    VALUE_GROUNDED_APPROXIMATELY = "value_grounded_approximately"

    # Not evaluated (VAL-17)
    #
    # There is deliberately no `value_absent`. The data model listed one, for a
    # constraint on a field with no value — and implementing it showed why it
    # cannot exist: an optional field left absent would then make every real
    # document `incomplete`, and the state would stop meaning "an obligation went
    # unchecked". A constraint constrains a value; where there is none there is no
    # obligation, and absence is the requiredness check's subject.
    OPERAND_ABSENT = "operand_absent"
    OPERAND_GROUP_ABSENT = "operand_group_absent"
    TYPE_MISMATCH = "type_mismatch"


#: The reasons that mean "this check could not run", as opposed to "it ran and
#: the document failed it". Kept as data so the verdict derivation and the
#: counts read from one list rather than two matching ``if`` chains.
NOT_EVALUATED_REASONS = frozenset(
    {
        ReasonCode.OPERAND_ABSENT,
        ReasonCode.OPERAND_GROUP_ABSENT,
        ReasonCode.TYPE_MISMATCH,
    }
)


class CheckOutcome(BaseModel):
    """One declared obligation, at one place, and what became of it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    check_id: str
    field_path: str
    kind: CheckKind
    outcome: Outcome

    #: ``None`` if and only if the check passed.
    reason: ReasonCode | None = None

    @model_validator(mode="after")
    def _reason_matches_outcome(self) -> CheckOutcome:
        if self.outcome is Outcome.PASSED and self.reason is not None:
            raise ValueError(f"check {self.check_id!r} passed and yet carries a reason")
        if self.outcome is not Outcome.PASSED and self.reason is None:
            raise ValueError(
                f"check {self.check_id!r} did not pass and must say why: a finding "
                "without a reason code cannot be counted or acted on"
            )
        return self


class Finding(BaseModel):
    """The non-passing view of a check, addressed to a field.

    ``message`` is redundant by construction (VAL-21): every fact it states is in
    a structured field beside it. That is the test -- if removing the prose lost
    information, the prose was carrying something a machine needed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    field_path: str
    check_id: str
    kind: CheckKind
    reason: ReasonCode
    severity: Severity

    #: The rule this finding came from, for a `rule` check; ``None`` otherwise.
    #:
    #: The id is also inside ``check_id`` (``rule:<id>@<anchor>``), and that was
    #: the only way to reach it until a convergence pass pointed out what that
    #: costs: a consumer grouping findings by rule had to split a composite
    #: string. FR-039's whole point is that no machine should have to parse
    #: anything, so the id is a field (FR-028).
    rule_id: str | None = None

    #: Rendered canonically, so two runs produce the same text for the same fact.
    expected: str | None = None
    actual: str | None = None

    #: Every field the check read, the anchor included. For a rule this is all of
    #: its operands, because naming only the anchor would hide half the evidence
    #: (FR-032).
    participants: tuple[str, ...] = ()

    #: **Copied** from the grounding outcome, never recomputed. This layer holds
    #: no document and could not compute a location if it wanted to (FR-038).
    span: Span | None = None
    pages: tuple[int, ...] = ()

    #: ``None`` means the parser supplied no geometry -- the same distinction
    #: Milestone 4 draws between unavailable and empty.
    geometry: tuple[Geometry, ...] | None = None

    message: str = ""

    @property
    def not_evaluated(self) -> bool:
        return self.reason in NOT_EVALUATED_REASONS


class ValidationCounts(BaseModel):
    """Totals that must add up, asserted here rather than trusted (VAL-27).

    The reconciliation is the point of the type. A rule that silently never ran
    shows up as a gap between ``declared`` and ``evaluated``, and putting the
    arithmetic in the model means no caller has to think to check it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    declared: int = 0
    evaluated: int = 0
    passed: int = 0
    failed: int = 0
    not_evaluated: int = 0

    errors: int = 0
    warnings: int = 0
    infos: int = 0

    @model_validator(mode="after")
    def _totals_reconcile(self) -> ValidationCounts:
        if self.declared != self.passed + self.failed + self.not_evaluated:
            raise ValueError(
                f"counts do not reconcile: declared={self.declared} but "
                f"passed+failed+not_evaluated="
                f"{self.passed + self.failed + self.not_evaluated}"
            )
        if self.evaluated != self.passed + self.failed:
            raise ValueError(
                f"counts do not reconcile: evaluated={self.evaluated} but "
                f"passed+failed={self.passed + self.failed}"
            )
        return self


class ValidationProvenance(BaseModel):
    """Everything needed to explain a verdict after every version has moved."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    extraction_artifact_id: str
    grounding_artifact_id: str
    schema_identity: str
    schema_hash: str
    rule_vocabulary_version: str
    pattern_dialect_version: str

    #: The rules this run actually evaluated, sorted. The chain carries what each
    #: rule *says* (it is inside ``schema_hash``); only this says which ran.
    enabled_rules: tuple[str, ...] = ()

    options: ValidationOptions
    validator_id: str
    validator_version: str


class ValidationResult(BaseModel):
    """The verdict, every check, the findings, the counts, and the provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    verdict: Verdict

    #: Every declared check, passed ones included (FR-011).
    checks: tuple[CheckOutcome, ...]

    #: The non-passing ones, in the total order of VAL-28.
    findings: tuple[Finding, ...]

    counts: ValidationCounts
    provenance: ValidationProvenance
    artifact_id: str

    def findings_at(self, severity: Severity) -> tuple[Finding, ...]:
        return tuple(finding for finding in self.findings if finding.severity is severity)

    def check(self, check_id: str) -> CheckOutcome | None:
        """One check by id — the "did this rule run?" question, answered directly."""
        return next((item for item in self.checks if item.check_id == check_id), None)

    # There is deliberately no `is_valid`, no `ok`, and no `__bool__`. Three
    # verdicts do not fit in one bit, and a convenience property that pretended
    # otherwise would be read far more often than the verdict itself (FR-042).
