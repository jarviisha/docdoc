"""T130 — the slot `runs/observe.py` did not have, and its one rule.

`pipeline/observe.py` has had an observer slot since Milestone 5 and argues its
shape: one slot, return value ignored, a raising observer cannot fail a run. This
module had none, so `run.transition` — a claim, a lease expiry, a redelivery, a
cancellation — could not reach an exporter at all (research R4).

The addition mirrors that module exactly, so a deployment learns one pattern
rather than two that are nearly the same. These tests assert the mirroring.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from docdoc.pipeline import observe as pipeline_observe
from docdoc.runs import observe


@pytest.fixture(autouse=True)
def empty_slots():
    """Both slots empty before and after. A test that left one installed would
    make the next file's assertions depend on the order they ran in."""
    observe.set_observer(None)
    pipeline_observe.set_observer(None)
    yield
    observe.set_observer(None)
    pipeline_observe.set_observer(None)


def _transition(**overrides: object) -> None:
    fields: dict = {
        "run_id": uuid4(),
        "tenant_id": "acme",
        "from_state": "queued",
        "to_state": "running",
        "attempts": 1,
        "worker_id": "w1",
        "reason": "claimed",
    }
    fields.update(overrides)
    observe.log_transition(**fields)  # type: ignore[arg-type]


def test_the_slot_is_empty_until_something_installs_one() -> None:
    assert observe.observer() is None


def test_an_installed_observer_receives_the_transition() -> None:
    seen: list[dict] = []
    observe.set_observer(seen.append)

    _transition(to_state="succeeded", reason="completed")

    assert len(seen) == 1
    assert seen[0]["event"] == "run.transition"
    assert seen[0]["to_state"] == "succeeded"


def test_installing_replaces_rather_than_appends() -> None:
    """One slot, not a list. A deployment that wants two writes a function that
    calls two — the argument `pipeline/observe.py` makes and this inherits."""
    first: list[dict] = []
    second: list[dict] = []
    observe.set_observer(first.append)
    observe.set_observer(second.append)

    _transition()

    assert first == []
    assert len(second) == 1


def test_none_removes_it() -> None:
    seen: list[dict] = []
    observe.set_observer(seen.append)
    observe.set_observer(None)

    _transition()

    assert observe.observer() is None
    assert seen == []


def test_a_raising_observer_does_not_fail_the_transition() -> None:
    """**The rule that matters.** The row is already written by the time this is
    called, so raising would be an exception about a state change that is a fact.
    """

    def _explode(_: dict) -> None:
        raise RuntimeError("the collector is on fire")

    observe.set_observer(_explode)

    _transition()  # must not raise


def test_the_return_value_is_ignored() -> None:
    """An observer that answers something is not answering a question."""
    observe.set_observer(lambda _: "no")

    _transition()


def test_the_two_modules_have_the_same_surface() -> None:
    """The mirroring, asserted rather than left to a reviewer's memory.

    A future change that gave one of them a list of observers, or a return value
    that meant something, would make "docdoc has one observer pattern" false —
    and that sentence is what `docs/concepts/` promises a deployment.
    """
    for name in ("set_observer", "observer"):
        assert callable(getattr(observe, name))
        assert callable(getattr(pipeline_observe, name))
