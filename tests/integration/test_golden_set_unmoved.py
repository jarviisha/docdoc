"""T171, SC-001 — **the criterion this milestone exists to satisfy.**

Milestone 10 adds seven operational capabilities and claims that none of them
changes a number docdoc produces. That claim is worth exactly as much as the test
behind it, so this is the test: score the golden set with every capability
enabled, score it with all of them disabled, and require the two reports to be
bit-identical.

**Why this is not obviously true.** Every capability here is off by default, so
the naive reading is that "all disabled" is the same code path as "not
installed". It is not, and two of the additions are why. Routing reads a
completed result and could — through one careless line — reach the extraction and
touch a value on the way past. And the observer slot `runs/observe.py` gained is
called on every state transition, so a bridge that mutated the payload it was
handed would be operating on a live mapping.

Both are exercised here by *enabling* them and comparing, rather than by
reasoning about them.

Offline. The `echo` adapter, documents supplied already parsed, no database, no
store, no network — because a criterion this central must run in the suite
everybody runs rather than in the one that needs infrastructure.
"""

from __future__ import annotations

import json

import pytest
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set, registry
from tests.fixtures.evaluation.predictions import RESPONSES, document_for

from docdoc.evaluation import evaluate
from docdoc.extraction.adapters import EchoAdapter
from docdoc.pipeline import observe as pipeline_observe
from docdoc.recording import record_predictions
from docdoc.runs import observe as runs_observe
from docdoc.runs.routing import RoutingPolicy, decide

FACTS = facts_for_fixtures()
DOCUMENTS = {name: document_for(name) for name in RESPONSES if name != "invoice@2"}

#: A policy that would send most of these to review if routing could touch
#: anything on its way past. It cannot, which is why it is enabled here.
POLICY = RoutingPolicy(
    version="unmoved@1",
    required_fields=frozenset({"total", "invoice_number", "invoice_date", "vendor"}),
    review_when_required_fuzzy_below=0.99,
)


@pytest.fixture(autouse=True)
def empty_slots():
    """Both observer slots empty before and after, so one test cannot leave an
    observer installed and change what the next one measures."""
    runs_observe.set_observer(None)
    pipeline_observe.set_observer(None)
    yield
    runs_observe.set_observer(None)
    pipeline_observe.set_observer(None)


def _record():
    """The golden set, recorded through the echo adapter."""
    return record_predictions(
        golden_set(),
        adapter=EchoAdapter.from_fixtures("tests/fixtures/echo"),
        registry=registry(),
        documents=DOCUMENTS,
    )


def _score(*, everything_on: bool) -> str:
    """Record and score, and return the report as canonical JSON.

    The comparison is over the **serialised** report rather than over an object,
    because that is what a reader actually compares two of: a report from before
    an upgrade and one from after.
    """
    seen: list[dict] = []

    if everything_on:
        # The observer slot this milestone added, occupied. A bridge that mutated
        # the mapping it was handed would be doing so on a live payload; this one
        # keeps a reference to it, which is as close as an observer can get.
        def _observe(payload: dict) -> None:
            seen.append(payload)

        runs_observe.set_observer(_observe)
        pipeline_observe.set_observer(_observe)

    predictions = _record()

    if everything_on:
        # Routing, over the same results, before they are scored. If `decide`
        # reached the extraction or recomputed anything, the report below moves.
        for result in predictions.predictions.values():
            decide(result, POLICY)
        assert seen, "the observer slot was not exercised"

    report = evaluate(golden_set(), predictions, facts=FACTS)
    return json.dumps(report.model_dump(mode="json"), sort_keys=True)


def test_the_golden_set_does_not_move() -> None:
    """**SC-001.** Bit-identical, and the comparison is over the report's bytes."""
    off = _score(everything_on=False)
    on = _score(everything_on=True)

    assert on == off, (
        "the golden-set report moved when Milestone 10's capabilities were "
        "enabled. Every one of them is operational and none may change a value, "
        "a verdict, a location, or an identity (SC-001)"
    )


def test_the_report_is_not_empty() -> None:
    """Guards the guard.

    Two empty reports compare equal, so an evaluation that scored nothing would
    make the assertion above pass while measuring nothing at all.
    """
    report = json.loads(_score(everything_on=False))

    assert report.get("metrics"), "the report carries no metrics"
    assert json.dumps(report).count("field_accuracy") > 0


def test_enabling_the_capabilities_actually_does_something() -> None:
    """The other half of the guard.

    If "with everything on" were the same code path as "with everything off", the
    comparison above would be between two identical runs and would prove nothing.
    """
    seen: list[dict] = []
    pipeline_observe.set_observer(seen.append)

    _record()

    assert seen, "the observer slot was never called, so nothing was enabled"


def test_routing_over_a_recorded_result_changes_nothing_about_it() -> None:
    """The narrower claim, stated directly (FR-073).

    `decide` is a pure function of a result and a policy, so calling it cannot
    move an identity. Asserted rather than argued, because the failure would be
    quiet: an implementation that recomputed something would produce the same
    answer for the same inputs and would still have written an artifact.
    """
    predictions = _record()

    for result in predictions.predictions.values():
        before = json.dumps(result.model_dump(mode="json"), sort_keys=True)
        decision = decide(result, POLICY)
        after = json.dumps(result.model_dump(mode="json"), sort_keys=True)

        assert after == before, "routing mutated the result it read"
        assert decision.policy_version == "unmoved@1"
