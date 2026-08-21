"""This stage's place in the ADR-0003 artifact chain.

    blob_id -> parse artifact (== document_id) -> extraction artifact
            -> grounding artifact -> **validation artifact**

The options hash folds four things: the rule vocabulary version, the pattern
dialect version, the enabled rule ids, and the grounding policy. Both halves of
that list matter.

What is folded, and why each can change a verdict: the vocabulary decides what a
rule *does*; the dialect decides what a ``pattern`` *means*; the enabled set
decides which rules ran at all; the policy decides whether an unlocated value is
acceptable.

What is **not** folded, and why it does not need to be: a rule's operands,
tolerance, and severity live on the rule, so they are inside ``schema_hash``,
inside the extraction artifact, inside the grounding artifact, and inside this
one by chaining. ADR-0003's Validate row says "enabled rule set and rule
versions"; rules are schema content, so ADR-0008 already versions them, and a
second per-rule counter would be a third answer to a question two identifiers
already answer. Recorded in the plan as a refinement to raise on that ADR rather
than resolved silently here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docdoc.kernel import canonical_json, content_id_for, options_hash_for
from docdoc.validation.pattern import PATTERN_DIALECT_VERSION
from docdoc.validation.rules import RULE_VOCABULARY_VERSION

if TYPE_CHECKING:
    from docdoc.validation.options import ValidationOptions

__all__ = [
    "VALIDATOR_ID",
    "VALIDATOR_VERSION",
    "options_hash_for_validation",
    "validation_artifact_id_for",
]

#: The processor id of this stage. Stable; the *version* is what moves.
VALIDATOR_ID = "deterministic-validator"

#: Moves whenever output changes for unchanged inputs -- which here means the
#: semantics of any check, a documented default severity, the verdict
#: derivation, the finding order, or the shape of a finding.
#: ``tests/unit/test_validator_version_snapshot.py`` is what makes that a build
#: failure rather than a review obligation (FR-050).
#:
#: ``1.1.0`` added ``Finding.rule_id``. A new field changes the output for
#: unchanged inputs, so it moves this number even though no verdict changed --
#: which is the rule taken literally rather than only when it is inconvenient.
VALIDATOR_VERSION = "1.1.0"


def options_hash_for_validation(
    options: ValidationOptions, *, enabled_rules: tuple[str, ...]
) -> str:
    """Identity of one validation configuration (FR-048).

    ``enabled_rules`` is passed in already resolved -- ``None`` in the options
    means "every rule this schema declares", and *which* rules those are is a
    property of the schema rather than of the options. Folding the resolved list
    is what makes "all rules" and "these three rules, which happen to be all of
    them" the same run rather than two runs that differ on paper.
    """
    policy = options.grounding_policy
    return options_hash_for(
        {
            "rule_vocabulary_version": RULE_VOCABULARY_VERSION,
            "pattern_dialect_version": PATTERN_DIALECT_VERSION,
            "enabled_rules": sorted(enabled_rules),
            "grounding_policy": {
                "ungrounded": None if policy.ungrounded is None else str(policy.ungrounded),
                "fuzzy": None if policy.fuzzy is None else str(policy.fuzzy),
                "exact": None if policy.exact is None else str(policy.exact),
            },
        }
    )


def validation_artifact_id_for(
    *,
    grounding_artifact_id: str,
    options_hash: str,
) -> str:
    """``sha256(input_artifact_id + processor_id + processor_version + options_hash)``.

    Chained from the *grounding* artifact, so this id transitively inherits the
    document, the parser, the schema, the prompt, the model, and the grounding
    threshold without naming any of them (ADR-0003).
    """
    payload = canonical_json(
        {
            "input_artifact_id": grounding_artifact_id,
            "processor_id": VALIDATOR_ID,
            "processor_version": VALIDATOR_VERSION,
            "options_hash": options_hash,
        }
    )
    return content_id_for(payload)
