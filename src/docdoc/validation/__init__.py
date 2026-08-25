"""Whether a located result is acceptable -- and nothing else.

Milestone 3 asked what the model found. Milestone 4 asked where it is. This
layer asks the third question, and Principle VII is the reason it is a separate
stage with its own artifact rather than a flag on either of the other two.

What it will not do, stated here because each is a temptation with a plausible
argument behind it:

* **It repairs nothing.** No value is corrected, clamped, coerced, rounded,
  trimmed, defaulted, or dropped -- on the success path or any failure path. A
  value that would pass only after adjustment fails (FR-004).
* **It asks no model anything.** A failing check is reported as failing. Asking
  a model to reconsider would make the same result verdict differently on two
  runs, which Principle III forbids for this stage.
* **It reads no document.** Every location a finding carries was computed by
  Milestone 4 and is copied through. Taking the document as well would create a
  second path to the same fact, and a way for two stages to disagree about where
  a value is (FR-005).
* **It decides nothing about what happens next.** Routing, escalation, and human
  review are policy built on this verdict, not part of producing it (FR-046).

No network, no credentials, no provider, no database. The whole layer is
deterministic code over two artifacts and a schema.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from docdoc.validation import enumerate as enumerate_checks
from docdoc.validation.constraints import (
    check_constraints,
    check_required,
    compile_declared_patterns,
)
from docdoc.validation.errors import ValidationError
from docdoc.validation.grounding_policy import check_grounding
from docdoc.validation.identity import (
    VALIDATOR_ID,
    VALIDATOR_VERSION,
    options_hash_for_validation,
    validation_artifact_id_for,
)
from docdoc.validation.observe import log_refusal, log_validation
from docdoc.validation.options import GroundingPolicy, ValidationOptions
from docdoc.validation.pattern import PATTERN_DIALECT_VERSION, PatternSyntaxError
from docdoc.validation.result import (
    CheckKind,
    CheckOutcome,
    Finding,
    Outcome,
    ReasonCode,
    Severity,
    ValidationCounts,
    ValidationProvenance,
    ValidationResult,
    Verdict,
)
from docdoc.validation.rules import RULE_VOCABULARY_VERSION, check_rules
from docdoc.validation.verdict import assemble, count, derive_verdict, sort_key

if TYPE_CHECKING:
    from docdoc.extraction.extract import ExtractionResult
    from docdoc.extraction.schema import Schema
    from docdoc.grounding.result import GroundingResult
    from docdoc.validation.record import CheckRecord

__all__ = [
    "PATTERN_DIALECT_VERSION",
    "RULE_VOCABULARY_VERSION",
    "VALIDATOR_ID",
    "VALIDATOR_VERSION",
    "CheckKind",
    "CheckOutcome",
    "Finding",
    "GroundingPolicy",
    "Outcome",
    "PatternSyntaxError",
    "ReasonCode",
    "Severity",
    "ValidationCounts",
    "ValidationError",
    "ValidationOptions",
    "ValidationProvenance",
    "ValidationResult",
    "Verdict",
    "resolve_enabled_rules",
    "validate",
]


def validate(
    extraction: ExtractionResult,
    grounding: GroundingResult,
    schema: Schema,
    *,
    options: ValidationOptions | None = None,
) -> ValidationResult:
    """Judge one located result against the schema it was extracted under.

    Exactly one result or an explicit error; never a partial verdict (FR-001).
    The three inputs are read and never modified, on the success path and on
    every failure path alike (FR-004).

    There is no ``document`` parameter, and that is a guarantee rather than an
    omission: every location a finding carries was computed by Milestone 4 and is
    copied through, so this stage cannot disagree with that one about where a
    value is (FR-005).

    Raises:
        ValidationError: the grounding result did not come from this extraction,
            the extraction was not produced under this schema, or the value tree
            does not fit it (FR-002, FR-018).
    """
    options = options or ValidationOptions()

    # A monotonic clock, and the one place this layer reads one. It measures the
    # run and never influences it: the duration reaches the log event and nothing
    # else, so Principle III's "no clock in the deterministic path" holds.
    started = time.perf_counter()

    _refuse_mismatched_inputs(extraction, grounding, schema, started=started)

    # Before the first check is enumerated: a pattern outside the dialect is an
    # authoring fault, and FR-056 is that it must never reach verdict time to
    # become a check nobody notices did not run.
    compile_declared_patterns(schema)

    index = enumerate_checks.walk(schema, extraction.values)
    enabled = _enabled_rules(schema, options)

    records: list[CheckRecord] = []
    absent_groups: list[str] = []
    for slot in index.slots:
        if slot.field.is_grouping and slot.group_absent:
            absent_groups.append(f"{slot.path}.")
        # FR-017: an absent group is *one* thing that is missing. Reporting its
        # children as missing too would turn one fact into five, and would make a
        # required field inside an optional group fail whenever that group is
        # legitimately absent — the nested-optionality trap.
        inside_absent_group = any(slot.path.startswith(prefix) for prefix in absent_groups)
        required = None if inside_absent_group else check_required(slot)
        if required is not None:
            records.append(required)
        records.extend(check_constraints(slot))
        grounded = check_grounding(
            slot, grounding.outcomes.get(slot.path), options.grounding_policy
        )
        if grounded is not None:
            records.append(grounded)
    records.extend(check_rules(schema, index, enabled=enabled))

    ordered = tuple(sorted(records, key=lambda item: sort_key(item, index)))
    checks, findings = assemble(ordered, grounding.outcomes)

    resolved = enabled_names(schema, enabled)
    options_hash = options_hash_for_validation(options, enabled_rules=resolved)
    result = ValidationResult(
        verdict=derive_verdict(ordered),
        checks=checks,
        findings=findings,
        counts=count(ordered),
        provenance=ValidationProvenance(
            document_id=grounding.provenance.document_id,
            extraction_artifact_id=extraction.artifact_id,
            grounding_artifact_id=grounding.artifact_id,
            schema_identity=schema.identity,
            schema_hash=extraction.provenance.schema_hash,
            rule_vocabulary_version=RULE_VOCABULARY_VERSION,
            pattern_dialect_version=PATTERN_DIALECT_VERSION,
            enabled_rules=resolved,
            options=options,
            validator_id=VALIDATOR_ID,
            validator_version=VALIDATOR_VERSION,
        ),
        artifact_id=validation_artifact_id_for(
            grounding_artifact_id=grounding.artifact_id,
            options_hash=options_hash,
        ),
    )
    log_validation(result, duration_ms=(time.perf_counter() - started) * 1000)
    return result


def resolve_enabled_rules(
    schema: Schema, options: ValidationOptions | None = None
) -> tuple[str, ...]:
    """The rule ids a run under these options would evaluate, resolved.

    The two steps ``validate`` takes internally, exposed as one, so that the
    pipeline can compute this stage's artifact id *before* running the stage
    (FR-012) without reaching for a private function or restating the resolution.
    Restating it is the specific hazard: the folded set would then live in two
    places, and the day they disagreed the store would answer with an artifact
    validated under a different rule set (FR-058).

    Raises what ``validate`` would raise for the same inputs — an ``enabled_rules``
    naming a rule the schema does not declare is a fault whenever it is noticed,
    and noticing it earlier is not a reason to soften it.
    """
    options = options or ValidationOptions()
    return enabled_names(schema, _enabled_rules(schema, options))


def enabled_names(schema: Schema, enabled: frozenset[str] | None) -> tuple[str, ...]:
    """The rule ids this run evaluated, sorted.

    Resolved from the schema rather than echoed from the options, so that "every
    rule" and "these three, which happen to be all of them" are the same run
    rather than two runs whose identities differ on paper (FR-048).
    """
    declared = tuple(rule.id for rule in schema.rules)
    if enabled is None:
        return tuple(sorted(declared))
    return tuple(sorted(name for name in declared if name in enabled))


def _enabled_rules(schema: Schema, options: ValidationOptions) -> frozenset[str] | None:
    """The requested subset, refusing a name the schema does not declare.

    Silently ignoring an unknown rule id would let a typo disable a rule while
    the run reported success — the disabled rule would simply not appear, which
    is indistinguishable from a rule nobody ever wrote (VAL-26).
    """
    if options.enabled_rules is None:
        return None
    declared = {rule.id for rule in schema.rules}
    unknown = sorted(options.enabled_rules - declared)
    if unknown:
        raise ValidationError(
            f"enabled_rules names {unknown}, which {schema.identity} does not declare. "
            f"It declares {sorted(declared)}",
            expected=str(sorted(declared)),
            actual=str(unknown),
        )
    return options.enabled_rules


def _refuse_mismatched_inputs(
    extraction: ExtractionResult,
    grounding: GroundingResult,
    schema: Schema,
    *,
    started: float,
) -> None:
    """FR-002 — three ways two artifacts can fail to belong together.

    Each is refused rather than validated anyway, because every one of them
    produces a verdict that is structurally valid and about the wrong thing: the
    fields line up, the checks run, and the answer describes a document that was
    never examined.
    """
    duration = (time.perf_counter() - started) * 1000
    if grounding.provenance.extraction_artifact_id != extraction.artifact_id:
        log_refusal(
            reason="grounding_is_of_another_extraction",
            expected=extraction.artifact_id,
            actual=grounding.provenance.extraction_artifact_id,
            duration_ms=duration,
        )
        raise ValidationError(
            "this grounding result did not come from this extraction result: grounding "
            f"names {grounding.provenance.extraction_artifact_id}, extraction is "
            f"{extraction.artifact_id}. Validating them together would judge one "
            "document's values against another document's locations",
            expected=extraction.artifact_id,
            actual=grounding.provenance.extraction_artifact_id,
        )
    if extraction.provenance.schema_identity != schema.identity:
        log_refusal(
            reason="schema_identity_mismatch",
            expected=schema.identity,
            actual=extraction.provenance.schema_identity,
            duration_ms=duration,
        )
        raise ValidationError(
            f"this result was extracted under {extraction.provenance.schema_identity}, "
            f"not {schema.identity}. A verdict computed against a different schema than "
            "the one that produced the values is structurally valid and meaningless",
            expected=schema.identity,
            actual=extraction.provenance.schema_identity,
        )
    from docdoc.extraction.identity import schema_hash_for

    current = schema_hash_for(schema)
    if extraction.provenance.schema_hash != current:
        log_refusal(
            reason="schema_hash_mismatch",
            expected=current,
            actual=extraction.provenance.schema_hash,
            duration_ms=duration,
        )
        raise ValidationError(
            f"{schema.identity} has been edited since this result was extracted: the "
            f"result records {extraction.provenance.schema_hash}, this schema hashes to "
            f"{current}. The version did not move, so nothing else can tell the two "
            "apart — which is exactly what ADR-0008 gives the hash to catch",
            expected=current,
            actual=extraction.provenance.schema_hash,
        )
