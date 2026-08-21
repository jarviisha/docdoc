"""Disclosure, driven by the tier and not by the caller's diligence.

FR-056 makes redaction a **property of the dataset**. That wording is the whole
design: if it were a caller argument, then a maintainer who forgot the flag would
publish a restricted corpus's contents in a report, and the dataset's terms would
be enforced by memory. Here the tier decides, and there is no argument to forget.

FR-026 requires every non-correct outcome to record the expected and the predicted
value "subject to FR-056". The hash is what satisfies it under this rule: a
restricted near-miss stays diagnosable as *different* -- two hashes that do not
match -- but not as *how*. That is the trade the dataset's terms impose, and it is
better than the alternatives, which are publishing the value or reporting nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docdoc.evaluation.tiers import Tier
from docdoc.kernel import content_id_for

if TYPE_CHECKING:
    from collections.abc import Iterable

    from docdoc.evaluation.outcomes import FieldOutcome

__all__ = ["redact_outcome", "redacted_tiers_in"]


def redact_outcome(outcome: FieldOutcome) -> FieldOutcome:
    """Replace values with hashes where the tier restricts disclosure.

    A no-op for the public tier. For the restricted tier the values are dropped
    and ``sha256`` over their canonical rendering takes their place.
    """
    if outcome.tier is not Tier.RESTRICTED:
        return outcome
    return outcome.model_copy(
        update={
            "expected": None,
            "predicted": None,
            "expected_hash": _hash(outcome.expected),
            "predicted_hash": _hash(outcome.predicted),
            "redacted": True,
        }
    )


def _hash(value: str | None) -> str | None:
    if value is None:
        return None
    return content_id_for(value.encode("utf-8"))


def redacted_tiers_in(outcomes: Iterable[FieldOutcome]) -> tuple[Tier, ...]:
    """Which tiers were redacted, for the report to state (FR-056).

    A report that redacted without saying so would be indistinguishable from one
    that had nothing to redact.
    """
    tiers = {outcome.tier for outcome in outcomes if outcome.redacted}
    return tuple(sorted(tiers, key=str))
