"""The settings a run used that can change its verdict -- and nothing else.

Both fields here change verdicts, so both fold into the stage's identity. The
test Milestone 4 set still applies: a timeout cannot change the content of a
successful result and stays out of identity; a policy that decides whether an
ungrounded value is acceptable obviously can, and goes in.

There is deliberately no run-level severity override. A rule's severity is
declared on the rule, so it already travels inside ``schema_hash`` and therefore
inside every artifact this stage chains from. Adding a second place to set it
would create two answers to one question.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from docdoc.validation.severity import Severity

__all__ = ["GroundingPolicy", "ValidationOptions"]


class GroundingPolicy(BaseModel):
    """What each grounding status means to a verdict (VAL-22, FR-035).

    The defaults say: a value nobody could locate is worth reporting but is not
    by itself a rejection, and an approximate location is worth recording. A
    deployment that disagrees says so explicitly -- and sees its artifact ids
    move, because it has changed what its verdicts mean.

    ``None`` emits no check at all, which is different from emitting one that
    passes: there is no obligation here, so there is nothing to count.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    ungrounded: Severity | None = Severity.WARNING
    fuzzy: Severity | None = Severity.INFO

    #: A located value needs no finding. Settable so a deployment can audit its
    #: exact-tier coverage, and ``None`` because normally nobody wants 200 infos.
    exact: Severity | None = None


class ValidationOptions(BaseModel):
    """Frozen, and folded into the stage's identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    grounding_policy: GroundingPolicy = GroundingPolicy()

    #: ``None`` means every rule the schema declares. A set names the subset to
    #: run; the ones left out are absent from ``checks`` and explained by the
    #: provenance record, which is what stops a disabled rule from looking like a
    #: rule that was never written (VAL-26).
    enabled_rules: frozenset[str] | None = None
