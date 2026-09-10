"""Two outcomes computed from signals docdoc itself produced.

Pure. It reads grounding status and score, validation severity and verdict, and
schema requiredness -- and never ``model_confidence``, which Principle II forbids
and ADR-0004 keeps separate. ``tests/unit/test_routing_ignores_model_confidence.py``
varies that field alone across a fixture set and requires the decision not to
move; that test **is** the requirement, and this module exists in a shape that
makes it pass by construction rather than by care.

**Why `model_confidence` is not a signal**, restated here because this is the
file where somebody would add it. A model's self-reported confidence is a number
the model chose, about its own output, with no external referent. Grounding is
docdoc's own measurement of whether a value appears in the document; validation
is docdoc's own evaluation of rules the operator wrote. Routing on the first
would make "send this to a human" a function of how confident a vendor's model
was feeling, and would change when the vendor changed the model. ADR-0004 keeps
the number visible and inert, and this is one of the places inertness is
enforced.

**Two outcomes, and not three** (FR-067a). `reject` states what a deployment
should *do*, which is the caller's decision and not docdoc's; `retry` would send
the result back into the pipeline, which FR-073 forbids and which is the whole
reason routing could be admitted to this milestone at all. What this produces is
a reading of a result, not a disposition of it.

**It computes no value and writes no artifact.** The decision is stored on the
run's projection, never in the ADR-0003 chain -- an artifact carrying it would
make ``processing_id`` a function of a threshold, so editing a policy would
change the identity of results computed before the edit.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "Routable",
    "RoutingDecision",
    "RoutingOutcome",
    "RoutingPolicy",
    "RoutingReason",
    "StoredResult",
    "decide",
    "policy_from_file",
]


class Routable(Protocol):
    """What `decide` reads: a grounding result and a validation result.

    **Narrower than `PipelineResult`, and that is the correction.** `decide` was
    annotated as taking one, and the HTTP route dutifully built one out of two
    artifacts it had read back from the store — fabricating an empty `outcomes`
    tuple and passing the *validation* provenance where a `RunProvenance` was
    required. Pydantic rejected it, so `GET /v1/runs/{run_id}` raised on every
    succeeded run once a policy was configured. The bug was found by the first
    test to exercise that route (T179); the live pass that checked the other
    scenarios never configured a policy.

    A `PipelineResult` satisfies this structurally, so every in-process caller is
    unaffected. What changed is that the signature now names the two things this
    function actually reads, which is what stopped the route having to invent the
    six it does not.
    """

    grounding: Any
    validation: Any


@dataclass(frozen=True)
class StoredResult:
    """A completed result reassembled from the store, for routing only.

    What a caller has after walking the artifact chain back: the two results
    `decide` reads, and nothing pretending to be a run. `_stored_result` in the
    HTTP layer already refuses to report stage outcomes for work a request did
    not do — *"reporting stage statuses for work this request did not do would be
    fiction"* — and this is that same refusal, applied to routing.
    """

    grounding: Any = None
    validation: Any = None


class RoutingOutcome(StrEnum):
    """Exactly two members, and the set is closed (FR-067).

    A third was considered twice and refused twice, for two different reasons
    that are both worth keeping:

    ``reject`` is a **disposition**, not a reading. Whether a doubtful invoice is
    rejected, held, or paid anyway is a business decision that belongs to the
    caller, and encoding it here would make docdoc's opinion about a document
    into an instruction about it.

    ``retry`` would send the result back into the pipeline. FR-073 forbids
    routing from re-entering it, and that prohibition is what makes routing
    admissible at all: a decision that could cause a second run would make the
    pipeline's output depend on a policy, and `processing_id` is derived from
    inputs.
    """

    AUTOMATIC = "automatic"
    REVIEW = "review"


class RoutingReason(BaseModel):
    """One signal that contributed, named so a human can act on it.

    Three fields, and `observed` is a **string**. A decision that returned a
    score would invite a caller to compare two of them, and ADR-0004 records that
    grounding scores are not comparable across tiers. What a reviewer needs is
    "`total` was ungrounded", not "`total` scored 0.71".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    field: str
    #: Which of the three permitted sources this came from. Closed by the policy
    #: rather than by an enum, because a new signal is a new policy field and
    #: this is only the label on it.
    signal: str
    observed: str


class RoutingDecision(BaseModel):
    """The reading, its reasons, and **the policy version that produced it**.

    The version is not decoration. Editing a policy alters no existing decision
    (FR-074): a stored decision says which rules produced it, so a deployment
    that tightened its thresholds last week can still explain last month's
    routing. Re-routing is an explicit act producing a new decision, not a
    recomputation that silently rewrites the old one.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    outcome: RoutingOutcome
    policy_version: str
    reasons: tuple[RoutingReason, ...] = ()


class RoutingPolicy(BaseModel):
    """**Data**, loaded from configuration. Not code, and not a plugin.

    Every field names a signal docdoc computed itself. There is deliberately no
    field for `model_confidence`, no field for a provider, and no field for a
    model name: a policy that could read any of them would make routing a
    function of which vendor answered.

    ``required_fields`` is how *schema requiredness* reaches a pure function.
    The alternative was handing `decide` a schema registry, which would make it
    do I/O — and a routing decision that read a schema from disk could not be
    replayed once the schema was withdrawn.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: Recorded on every decision this policy produces (FR-070). A deployment
    #: that edits thresholds without moving this has made its own audit trail
    #: unreadable, which is why it is required rather than defaulted.
    version: str

    #: Verdicts that send a result to review. `incomplete` is in the default set
    #: for the reason it exists at all: a run whose rules could not be evaluated
    #: is not a run that passed.
    review_verdicts: frozenset[str] = frozenset({"invalid", "incomplete"})

    #: Any finding at `error` severity. `warning` and `info` are deliberately
    #: powerless here for the same reason they are powerless in validation.
    review_on_error_finding: bool = True

    #: A required field that grounding could not place in the document.
    review_when_required_ungrounded: bool = True

    #: A required field grounded only fuzzily, below this similarity. `None`
    #: disables the check — and it is `None` by default, because a threshold
    #: nobody chose is a threshold nobody can defend.
    review_when_required_fuzzy_below: float | None = Field(default=None, ge=0.0, le=1.0)

    #: Which fields the schema requires. Empty means the two checks above find
    #: nothing, which is the honest behaviour for a policy that was not told
    #: what matters.
    required_fields: frozenset[str] = frozenset()


def policy_from_file(path: Path) -> RoutingPolicy:
    """Load a policy, refusing anything it cannot make sense of.

    Strict for the reason `KeyRing.from_file` is: every failure here is a
    deployment that believes it is routing and is not, and the quiet version —
    skipping an unreadable field and starting anyway — routes everything
    `automatic` while an operator believes their thresholds are in force.
    """
    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError(f"{path} must be a JSON object describing one routing policy")
    return RoutingPolicy.model_validate(raw)


def decide(result: Routable, policy: RoutingPolicy) -> RoutingDecision:
    """Read a completed result and say whether a human should look at it.

    **A pure function.** No clock, no store, no network, no artifact written. The
    same result and the same policy version produce the same decision on any
    machine at any time, which is what makes FR-074's "editing a policy alters no
    existing decision" checkable rather than asserted.

    The reasons are accumulated rather than short-circuited. A result sent to
    review for four reasons should say four, because the reviewer's first
    question is *what* is wrong and a single reason would send them looking at
    one field of several.
    """
    reasons: list[RoutingReason] = []

    validation = result.validation
    if validation is not None:
        verdict = str(validation.verdict)
        if verdict in policy.review_verdicts:
            reasons.append(RoutingReason(field="", signal="validation", observed=verdict))
        if policy.review_on_error_finding:
            reasons.extend(
                RoutingReason(field=finding.field_path, signal="validation", observed="error")
                for finding in validation.findings
                if str(finding.severity) == "error"
            )

    grounding = result.grounding
    if grounding is not None and policy.required_fields:
        for field in sorted(policy.required_fields):
            outcome = grounding.outcomes.get(field)
            if outcome is None:
                # A required field grounding has no entry for is one the model
                # reported absent. That is a fact about the document, and it is
                # exactly the case a reviewer is for.
                if policy.review_when_required_ungrounded:
                    reasons.append(
                        RoutingReason(field=field, signal="grounding", observed="absent")
                    )
                continue

            status = str(outcome.status)
            if status == "ungrounded" and policy.review_when_required_ungrounded:
                reasons.append(
                    RoutingReason(field=field, signal="grounding", observed="ungrounded")
                )
            elif (
                status == "fuzzy"
                and policy.review_when_required_fuzzy_below is not None
                and (outcome.score or 0.0) < policy.review_when_required_fuzzy_below
            ):
                # The *status*, not the score. A reason carrying `0.71` would
                # invite a caller to compare it against another field's, and
                # ADR-0004 records that these numbers are not comparable.
                reasons.append(RoutingReason(field=field, signal="grounding", observed="fuzzy"))

    return RoutingDecision(
        outcome=RoutingOutcome.REVIEW if reasons else RoutingOutcome.AUTOMATIC,
        policy_version=policy.version,
        reasons=tuple(reasons),
    )


# ---------------------------------------------------------------------------
# `model_confidence` IS READ NOWHERE ABOVE, AND THAT IS THE REQUIREMENT.
#
# This is the file where it would be added, by somebody who had a result in hand
# with a confidence on it and a reviewer asking for fewer false positives. The
# number is available: `result.extraction` carries it per value, and reading it
# would take one line and would appear to work.
#
# It is forbidden by Principle II and by ADR-0004, and the reason is not
# stylistic. A model's confidence is a number the model chose about its own
# output, with no external referent — so a deployment routing on it would find
# its review rate change when a vendor shipped a new checkpoint, with no change
# to any document, any schema, or any policy. Grounding and validation are
# docdoc's own measurements, and they are the only things here that can be
# argued with.
#
# `tests/unit/test_routing_ignores_model_confidence.py` varies that field alone
# and requires the decision not to move (SC-017).
# ---------------------------------------------------------------------------
