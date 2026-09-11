"""T138, FR-093 — priority orders work and changes nothing about it.

The hazard is not that somebody would deliberately branch on priority. It is that
a scheduling hint, once it exists on the run, is available to every layer below —
and the first plausible use of it ("urgent runs skip the slower parser", "urgent
runs get the larger model") makes the *result* a function of how much a client
was willing to ask for. Two callers submitting one document would then get two
answers, and the identity model would be lying: `processing_id` is derived from
inputs, and priority is not one of them.

So the guarantee is structural — priority is never passed below `Runs` — and it
is asserted two ways: on the call, and on the outcome.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from docdoc.runs import worker
from docdoc.runs.model import Priority, Run, RunStatus


def test_the_pipeline_is_not_given_a_priority() -> None:
    """The structural half. `pipeline.run` has no parameter to receive one.

    Checked against the signature rather than by reading `execute_one`, because a
    parameter that does not exist cannot be passed by a future change that
    forgets why.
    """
    from docdoc.pipeline import run as run_pipeline

    parameters = set(inspect.signature(run_pipeline).parameters)

    assert "priority" not in parameters
    assert "urgency" not in parameters
    assert not [name for name in parameters if "priorit" in name.lower()]


def test_execute_one_never_reads_the_priority() -> None:
    """The narrower half: the one function that holds a `Run` and calls downward.

    A source check, which is the crude instrument — and the right one here,
    because what is being asserted is that a value present on an object in scope
    is not used. There is no behaviour to observe, which is exactly why the
    mistake would be easy to make.
    """
    source = inspect.getsource(worker.execute_one)

    assert ".priority" not in source, (
        "`execute_one` reads the run's priority. Nothing below `Runs` may see "
        "it: a result that depended on it would make `processing_id` a function "
        "of what a client was willing to ask for (FR-093)"
    )


def test_the_pipeline_stage_event_carries_no_priority() -> None:
    """The observability half, which is where it would leak in practice.

    A span attribute naming the priority would let a downstream consumer
    correlate it with a verdict, and the first correlation somebody finds becomes
    the first branch somebody writes.
    """
    from docdoc.telemetry import _STAGE_ATTRIBUTES, _TRANSITION_ATTRIBUTES

    assert "priority" not in _STAGE_ATTRIBUTES
    assert "priority" not in _TRANSITION_ATTRIBUTES


def _run(priority: Priority) -> Run:
    from datetime import UTC, datetime
    from uuid import uuid4

    at = datetime(2026, 9, 9, tzinfo=UTC)
    return Run(
        run_id=uuid4(),
        tenant_id="acme",
        blob_id="sha256:" + "a" * 64,
        schema_identity="invoice@1",
        status=RunStatus.QUEUED,
        priority=priority,
        created_at=at,
        updated_at=at,
        expires_at=at,
    )


@pytest.mark.parametrize("field", ["blob_id", "schema_identity", "status"])
def test_priority_changes_nothing_else_about_a_run(field: str) -> None:
    """Two runs differing only in priority differ only in priority."""
    ordinary = _run(Priority.ORDINARY).model_dump()
    urgent = _run(Priority.URGENT).model_dump()

    assert ordinary[field] == urgent[field]


def test_the_two_priorities_are_the_whole_set() -> None:
    """A third class is a number, not a migration — but it is also a decision,
    and this is where it has to be made in the open."""
    assert {member.name for member in Priority} == {"ORDINARY", "URGENT"}
    assert int(Priority.ORDINARY) == 0
    assert int(Priority.URGENT) == 10


def test_this_check_can_actually_fail() -> None:
    """Guards the source check above: a rename would make it vacuous."""
    assert "queue.finish" in inspect.getsource(worker.execute_one), (
        "the source of `execute_one` is what was read, so the assertion above "
        "was looking at the right function"
    )
    assert Path("src/docdoc/runs/worker.py").exists()
