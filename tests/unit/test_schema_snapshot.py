"""T044 — the FR-017 change detector.

ADR-0008 is explicit that no system can detect a *semantic* change on its own.
Classification stays a human judgment; this check exists to guarantee the judgment
is **made** rather than skipped.

It is a change detector, not a breakage detector. Both bump-worthy and
non-bump-worthy edits trip it, and that is intended: the contributor clears it
either by publishing a new major or by refreshing the snapshot with the
classification stated in the commit message.

The failure message below matters as much as the assertion. A failing check whose
remedy is unclear gets bypassed, and a bypassed check protects nothing.
"""

from __future__ import annotations

import json
import pathlib

from docdoc.extraction import SchemaRegistry

SNAPSHOT = pathlib.Path("tests/fixtures/snapshots/schema_hashes.json")

_REMEDY = """
A registered schema version's content hash has moved. That is either:

  (a) a BREAKING change -- publish it as a new major instead. Add
      schemas/<name>@<next>.json and its prompt, and leave the old files alone.
      A stored result that names the old version must stay interpretable.

  (b) a NON-BREAKING change -- an added optional field, a loosened constraint, a
      reworded description. Refresh this snapshot and state the classification in
      the commit message:

          uv run python tests/unit/test_schema_snapshot.py

ADR-0008 has the full bump table. Do not clear this by editing the assertion.
"""


def _current() -> dict[str, str]:
    registry = SchemaRegistry.from_paths(["schemas"])
    return {identity: registry.resolve(identity).schema_hash for identity in registry.identities()}


def test_the_snapshot_exists_and_is_not_empty() -> None:
    """A missing snapshot would make every other assertion here vacuous."""
    assert SNAPSHOT.is_file(), f"{SNAPSHOT} is the change detector; it must be committed"
    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert recorded, "an empty snapshot detects nothing"


def test_no_registered_version_has_moved_its_hash() -> None:
    """The check itself."""
    recorded: dict[str, str] = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    current = _current()

    moved = {
        identity: (recorded[identity], current[identity])
        for identity in recorded.keys() & current.keys()
        if recorded[identity] != current[identity]
    }
    assert not moved, (
        "schema hash changed for: "
        + ", ".join(f"{i} ({was[:16]}… -> {now[:16]}…)" for i, (was, now) in sorted(moved.items()))
        + _REMEDY
    )


def test_a_new_identity_must_be_added_to_the_snapshot() -> None:
    """Otherwise a new schema is unguarded from the day it lands."""
    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    unguarded = sorted(_current().keys() - recorded.keys())
    assert not unguarded, (
        f"these schemas are registered but not in the snapshot: {unguarded}. "
        "Add them, so a later change to them is detected." + _REMEDY
    )


def test_a_removed_identity_must_be_removed_from_the_snapshot() -> None:
    """A stale entry is a check that passes for a schema nobody serves."""
    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    stale = sorted(recorded.keys() - _current().keys())
    assert not stale, (
        f"these snapshot entries name no registered schema: {stale}. "
        "Retiring a version is a decision; record it by removing the entry."
    )


def test_the_detector_actually_detects() -> None:
    """Guards the guard.

    A snapshot check that could not fail is the most comfortable kind of dead
    test. This mutates a schema in memory and confirms the comparison notices.
    """
    registry = SchemaRegistry.from_paths(["schemas"])
    entry = registry.resolve("invoice@1")
    first, *rest = entry.schema.fields
    mutated = entry.schema.model_copy(
        update={"fields": (first.model_copy(update={"description": "moved"}), *rest)}
    )

    from docdoc.extraction import schema_hash_for

    recorded = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    assert schema_hash_for(mutated) != recorded["invoice@1"]


if __name__ == "__main__":  # pragma: no cover - the refresh path
    SNAPSHOT.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT.write_text(json.dumps(_current(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {SNAPSHOT}")
