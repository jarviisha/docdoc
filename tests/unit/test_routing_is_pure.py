"""T153, T154, T155 — purity, the closed outcome set, and non-retroactivity.

Three properties of `decide` that share one fixture and one argument, which is
why they share a file.

**Pure** (FR-071, FR-073). No clock, no store, no network, no artifact written.
That is what lets a decision be replayed: a run routed to review last month can
be explained today by handing the same result to the same policy version.

**Two outcomes** (FR-067), asserted by name, and not configurable. The set being
closed is what stops `reject` — a disposition the caller owns — and `retry`,
which would re-enter the pipeline.

**Editing a policy alters no existing decision** (FR-074). Re-routing is an
explicit act producing a new decision, not a recomputation that rewrites the old
one, and the version on every decision is what makes the difference legible.
"""

from __future__ import annotations

import socket

import pytest
from pydantic import ValidationError

from docdoc.grounding.result import GroundingOutcome, GroundingStatus
from docdoc.runs.routing import (
    RoutingDecision,
    RoutingOutcome,
    RoutingPolicy,
    RoutingReason,
    decide,
)
from docdoc.validation.result import Verdict


class _Grounding:
    def __init__(self, outcomes: dict) -> None:
        self.outcomes = outcomes


class _Validation:
    def __init__(self, verdict: Verdict, findings: tuple = ()) -> None:
        self.verdict = verdict
        self.findings = findings


class _Result:
    def __init__(self, grounding=None, validation=None) -> None:
        self.grounding = grounding
        self.validation = validation
        self.outcomes = ()
        self.processing_id = "sha256:" + "d" * 64


def _result(*, total: tuple[str, float | None], verdict: Verdict = Verdict.VALID) -> _Result:
    return _Result(
        grounding=_Grounding(
            {
                "total": GroundingOutcome(
                    field_path="total",
                    status=GroundingStatus(total[0]),
                    score=total[1],
                )
            }
        ),
        validation=_Validation(verdict),
    )


POLICY = RoutingPolicy(version="default@1", required_fields=frozenset({"total"}))


# -- T154: exactly two outcomes ----------------------------------------------


def test_the_outcome_set_has_exactly_two_members_by_name() -> None:
    """FR-067. By name, so adding one is a failing test rather than a review note."""
    assert {member.name for member in RoutingOutcome} == {"AUTOMATIC", "REVIEW"}
    assert {str(member) for member in RoutingOutcome} == {"automatic", "review"}


def test_there_is_no_reject_and_no_retry() -> None:
    """The two that were considered and refused, each for its own reason.

    `reject` states what a deployment should *do*, which is the caller's
    decision. `retry` would send the result back into the pipeline, which FR-073
    forbids and which is the whole reason routing was admissible at all.
    """
    names = {member.value for member in RoutingOutcome}

    assert "reject" not in names
    assert "retry" not in names
    assert "hold" not in names
    assert "escalate" not in names


def test_the_outcome_set_is_not_configurable() -> None:
    """A policy that could add an outcome would make the set open by another name."""
    assert "outcome" not in RoutingPolicy.model_fields
    assert "outcomes" not in RoutingPolicy.model_fields
    with pytest.raises(ValidationError):
        RoutingPolicy(version="x", outcomes=["reject"])  # type: ignore[call-arg]


# -- T153: pure ---------------------------------------------------------------


def test_the_same_inputs_give_an_identical_decision() -> None:
    first = decide(_result(total=("ungrounded", None)), POLICY)
    second = decide(_result(total=("ungrounded", None)), POLICY)

    assert first == second


def test_it_reaches_no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """The same technique `test_scoring_is_offline.py` uses for evaluation."""

    def _refuse(*_: object, **__: object):
        raise AssertionError("routing opened a socket")

    monkeypatch.setattr(socket, "socket", _refuse)
    monkeypatch.setattr(socket, "create_connection", _refuse)
    monkeypatch.setattr(socket, "getaddrinfo", _refuse)

    decide(_result(total=("ungrounded", None)), POLICY)


def test_it_reads_no_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    """A decision that read a clock could not be replayed, and replay is the
    whole of FR-074: explaining last month's routing needs this month's code to
    reach the same answer from the same inputs."""
    import time

    def _refuse(*_: object, **__: object):
        raise AssertionError("routing read a clock")

    monkeypatch.setattr(time, "time", _refuse)
    monkeypatch.setattr(time, "monotonic", _refuse)

    decide(_result(total=("fuzzy", 0.5)), POLICY)


def test_it_writes_no_artifact(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """**The one that matters most** (FR-073).

    An artifact carrying the decision would make `processing_id` a function of a
    threshold, so editing a policy would change the identity of results computed
    before the edit — and the whole ADR-0003 chain would stop being derivable
    from inputs.
    """
    import builtins

    real_open = builtins.open

    def _refuse(*args: object, **kwargs: object):
        mode = str(kwargs.get("mode", args[1] if len(args) > 1 else "r"))
        if any(flag in mode for flag in "wxa+"):
            raise AssertionError(f"routing wrote a file: {args[:1]}")
        return real_open(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(builtins, "open", _refuse)

    decide(_result(total=("ungrounded", None)), POLICY)


def test_decide_takes_two_arguments_and_no_store() -> None:
    """A store parameter would be an invitation, and there is none to accept."""
    import inspect

    parameters = set(inspect.signature(decide).parameters)

    assert parameters == {"result", "policy"}


# -- T155: editing a policy is not retroactive --------------------------------


def test_a_decision_records_the_policy_version_that_produced_it() -> None:
    decision = decide(_result(total=("ungrounded", None)), POLICY)

    assert decision.policy_version == "default@1"


def test_editing_a_policy_alters_no_existing_decision() -> None:
    """FR-074. A stored decision is a fact about a moment, not a live query.

    The mechanism is that a decision is a **value**: `decide` returns one and
    nothing holds a reference back to the policy. Tightening the policy produces
    a *new* decision when somebody asks for one, and the old one still says what
    it said and which rules said it.
    """
    result = _result(total=("fuzzy", 0.5))
    before = decide(result, POLICY)

    tightened = POLICY.model_copy(
        update={"version": "default@2", "review_when_required_fuzzy_below": 0.9}
    )
    after = decide(result, tightened)

    assert before.outcome is RoutingOutcome.AUTOMATIC
    assert after.outcome is RoutingOutcome.REVIEW, "the new policy does decide differently"
    assert before.policy_version == "default@1", "and the old decision is untouched"
    assert before.reasons == ()


def test_a_decision_is_frozen() -> None:
    """A decision somebody could edit in place is a decision nobody can cite."""
    decision = decide(_result(total=("ungrounded", None)), POLICY)

    with pytest.raises(ValidationError):
        decision.outcome = RoutingOutcome.AUTOMATIC  # type: ignore[misc]


def test_a_reason_names_a_field_a_signal_and_an_observation() -> None:
    """And `observed` is a **string**, never a score.

    A decision returning `0.71` would invite a caller to compare it against
    another field's, and ADR-0004 records that grounding scores are not
    comparable across tiers.
    """
    decision = decide(_result(total=("ungrounded", None)), POLICY)

    assert decision.reasons == (
        RoutingReason(field="total", signal="grounding", observed="ungrounded"),
    )
    assert isinstance(decision.reasons[0].observed, str)


def test_all_reasons_are_reported_and_not_only_the_first() -> None:
    """A reviewer's first question is *what* is wrong, and one reason of four
    would send them looking at one field of several."""
    result = _result(total=("ungrounded", None), verdict=Verdict.INVALID)

    decision = decide(result, POLICY)

    assert len(decision.reasons) == 2
    assert {reason.signal for reason in decision.reasons} == {"grounding", "validation"}


def test_an_unconfigured_policy_routes_nothing_to_review() -> None:
    """`required_fields` empty means the grounding checks find nothing.

    The honest behaviour for a policy that was not told what matters: it is not a
    licence to send everything to a human, and it is not a claim that everything
    is fine — it is that this policy has nothing to say about grounding.
    """
    bare = RoutingPolicy(version="bare@1")

    decision = decide(_result(total=("ungrounded", None)), bare)

    assert decision.outcome is RoutingOutcome.AUTOMATIC
    assert isinstance(decision, RoutingDecision)
