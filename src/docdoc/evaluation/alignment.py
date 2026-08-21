"""Which predicted repeating-group entry is compared against which expected one.

Positional by declared order is the default. Where the golden set declares a key
for a group, entries align by that key and unmatched entries on either side become
*missing entries* and *spurious entries*.

**The key is declared by the dataset, never by the schema** (FR-020). A key in the
schema would move ``schema_hash`` under ADR-0008, and FR-004 would then refuse
every label already written against that schema — so declaring a key to fix
alignment would invalidate the dataset it was meant to measure. The act of
improving the measurement would destroy the thing being measured.

The entry-count mismatch is reported as **its own fact** (FR-021), not only
through the field outcomes it causes. Positional alignment downstream of a missing
entry shifts every later entry by one, producing field-level wreckage that reads
as many independent errors when it is one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from docdoc.evaluation.errors import EvaluationError

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "ENTRY_ALIGNMENT_VERSIONS",
    "EntryAlignment",
    "GroupOutcome",
    "align",
]

#: Both policies are versioned even though the default has no alternative yet,
#: because FR-020 requires the policy to be explicit, documented, and versioned —
#: and a change of alignment changes numerators.
POSITIONAL = "positional@1"
KEYED = "keyed@1"

ENTRY_ALIGNMENT_VERSIONS = (POSITIONAL, KEYED)


class EntryAlignment(BaseModel):
    """The policy that aligned one group's entries (EVA-13)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    group_path: str
    policy: str
    key_field: str | None = None


class GroupOutcome(BaseModel):
    """One repeating group's entry-level facts (EVA-16)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    group_path: str
    expected_entries: int
    predicted_entries: int
    missing_entries: int
    spurious_entries: int
    alignment: EntryAlignment

    @property
    def count_matches(self) -> bool:
        return self.expected_entries == self.predicted_entries


def align(
    *,
    document_id: str,
    group_path: str,
    expected_keys: Sequence[Any],
    predicted_keys: Sequence[Any],
    key_field: str | None,
) -> tuple[list[tuple[int | None, int | None]], GroupOutcome]:
    """Pair expected entry indices with predicted ones.

    Returns the pairing and the group's own reported facts. A pair of
    ``(expected_index, None)`` is a missing entry; ``(None, predicted_index)`` is
    a spurious one.

    ``expected_keys`` and ``predicted_keys`` carry the key value of each entry
    when a key is declared, and are otherwise unused — positional alignment reads
    only their lengths.
    """
    if key_field is None:
        pairs = _positional(len(expected_keys), len(predicted_keys))
        policy = POSITIONAL
    else:
        _refuse_duplicate_keys(document_id, group_path, key_field, expected_keys, "expected")
        _refuse_duplicate_keys(document_id, group_path, key_field, predicted_keys, "predicted")
        pairs = _keyed(expected_keys, predicted_keys)
        policy = KEYED

    missing = sum(1 for expected, predicted in pairs if predicted is None)
    spurious = sum(1 for expected, predicted in pairs if expected is None)

    return pairs, GroupOutcome(
        document_id=document_id,
        group_path=group_path,
        expected_entries=len(expected_keys),
        predicted_entries=len(predicted_keys),
        missing_entries=missing,
        spurious_entries=spurious,
        alignment=EntryAlignment(group_path=group_path, policy=policy, key_field=key_field),
    )


def _positional(expected: int, predicted: int) -> list[tuple[int | None, int | None]]:
    pairs: list[tuple[int | None, int | None]] = [
        (index, index) for index in range(min(expected, predicted))
    ]
    pairs.extend((index, None) for index in range(predicted, expected))
    pairs.extend((None, index) for index in range(expected, predicted))
    return pairs


def _keyed(
    expected_keys: Sequence[Any], predicted_keys: Sequence[Any]
) -> list[tuple[int | None, int | None]]:
    predicted_by_key = {key: index for index, key in enumerate(predicted_keys)}
    pairs: list[tuple[int | None, int | None]] = []
    matched: set[int] = set()

    for expected_index, key in enumerate(expected_keys):
        predicted_index = predicted_by_key.get(key)
        if predicted_index is None:
            pairs.append((expected_index, None))
        else:
            pairs.append((expected_index, predicted_index))
            matched.add(predicted_index)

    pairs.extend((None, index) for index in range(len(predicted_keys)) if index not in matched)
    return pairs


def _refuse_duplicate_keys(
    document_id: str,
    group_path: str,
    key_field: str,
    keys: Sequence[Any],
    side: str,
) -> None:
    """A duplicated key makes alignment ambiguous, and ambiguity picks silently.

    Refused rather than resolved by a tie-break, because any tie-break here would
    be an invented rule deciding which of two identical-looking entries the truth
    was about (EVA-13a).
    """
    seen: set[Any] = set()
    for key in keys:
        if key in seen:
            raise EvaluationError(
                f"repeating group {group_path!r} aligns by {key_field!r}, but the "
                f"{side} entries repeat the key {key!r}; alignment would have to "
                "guess which entry the truth describes",
                document_id=document_id,
                field_path=f"{group_path}.{key_field}",
                actual=str(key),
            )
        seen.add(key)
