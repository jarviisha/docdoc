"""SC-023 — `RunStatus` gains no member, and a client may still switch on it.

Milestone 9 closed this set at five and left `expired` out with a reason that has
now expired itself: *"a state no code path could reach is a state that lies to
everyone who reads the enum. Retention is Milestone 10's."* Milestone 10 builds
that sweep, so the state is reachable, and the obvious move is to add it.

FR-004a forbids it, and the argument is that adding it would swap one lie for
another. A `Run` carrying `expired` carries none of a run's other fields — no
blob, no schema, no stage outcomes — because they were deleted. What remains of a
removed run is a `Tombstone`: four fields, its own table, its own lookup. A
removed run is gone *as a run*.

The practical stake is a client's `match` over five names, and a CLI that renders
them. This file is what keeps that exhaustive.
"""

from __future__ import annotations

from docdoc.runs.model import TERMINAL_STATES, RunStatus, Tombstone


def test_the_five_names_and_no_others() -> None:
    """By name, so a rename fails as loudly as an addition."""
    assert {status.value for status in RunStatus} == {
        "queued",
        "running",
        "succeeded",
        "failed",
        "cancelled",
    }, (
        "RunStatus has changed. FR-004a closes it at five: a removed run is "
        "reported as a Tombstone, not as a run in a sixth state, because the "
        "value would carry none of a run's other fields."
    )


def test_no_member_names_a_removal() -> None:
    """The specific additions this milestone makes tempting."""
    names = {status.value for status in RunStatus}
    for forbidden in ("expired", "erased", "deleted", "purged", "needs_review"):
        assert forbidden not in names, (
            f"{forbidden!r} is not a run state. Ageing out and being erased are "
            "distinguished by `Tombstone.policy`, and routing lives in its own "
            "field with its own two outcomes."
        )


def test_the_terminal_set_is_unchanged() -> None:
    """The sweep only ever considers these three (FR-003)."""
    assert {status.value for status in TERMINAL_STATES} == {
        "succeeded",
        "failed",
        "cancelled",
    }


def test_a_tombstone_carries_four_fields_and_refuses_a_fifth() -> None:
    """FR-004: the shortness is the specification, so it is asserted."""
    assert set(Tombstone.model_fields) == {"run_id", "tenant_id", "deleted_at", "policy"}
