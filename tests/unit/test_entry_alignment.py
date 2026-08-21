"""T028 — which predicted entry is compared against which expected one (FR-020, FR-021).

Alignment decides numerators. The same prediction and the same labels produce
different accuracy depending on how entries are paired, which is why the policy
is explicit, documented, and versioned rather than implied by a loop.

**The key is declared by the golden set, never by the schema.** A key in the
schema would move ``schema_hash`` under ADR-0008, and FR-004 would then refuse
every label already written against that schema -- so the act of declaring a key
to fix alignment would invalidate the dataset it was meant to measure. That is
not a subtle trade-off; it is a circular dependency, and the fixture below shows
both halves: the *same* documents and the *same* predictions score 25/36
positionally and 27/36 keyed, with nothing but a dataset declaration between them.

The entry-count mismatch is reported as **its own fact**, not only through the
field outcomes it causes. Positional alignment downstream of a missing entry
shifts every later entry by one and produces field-level wreckage that reads as
many independent errors when it is one -- and a maintainer reading twelve wrong
line items has no way to see that the answer is "one entry was dropped".
"""

from __future__ import annotations

import pytest

from docdoc.evaluation import EvaluationError, FieldOutcomeKind, evaluate
from docdoc.evaluation.alignment import KEYED, POSITIONAL, align
from tests.fixtures.evaluation.datasets import (
    facts_for_fixtures,
    golden_set,
    keyed_golden_set,
)
from tests.fixtures.evaluation.predictions import prediction_set


def _outcomes_for(report: object, document_id: str, prefix: str) -> dict[str, FieldOutcomeKind]:
    return {
        outcome.field_path: outcome.kind
        for outcome in report.outcomes  # type: ignore[attr-defined]
        if outcome.document_id == document_id and outcome.field_path.startswith(prefix)
    }


# -- positional, the default -------------------------------------------------


def test_positional_alignment_pairs_by_declared_order() -> None:
    pairs, outcome = align(
        document_id="d",
        group_path="line_items",
        expected_keys=[0, 1, 2],
        predicted_keys=[0, 1, 2],
        key_field=None,
    )

    assert pairs == [(0, 0), (1, 1), (2, 2)]
    assert outcome.alignment.policy == POSITIONAL
    assert outcome.count_matches


def test_positional_alignment_is_the_fixture_default() -> None:
    """The dataset declaring no key gets positional, and the report says which."""
    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())

    policies = {group.alignment.policy for group in report.metrics.group_outcomes}
    assert policies == {POSITIONAL}


def test_a_shifted_entry_is_wrong_positionally_and_right_by_key() -> None:
    """The whole argument for FR-020, in one comparison.

    Identical predictions, identical labels. The only difference is a line in the
    golden set, and it moves two outcomes from ``INCORRECT`` to ``CORRECT``.
    Nothing about the pipeline changed; what changed is the question being asked.
    """
    facts = facts_for_fixtures()
    predictions = prediction_set()

    positional = evaluate(golden_set(), predictions, facts=facts)
    keyed = evaluate(keyed_golden_set(), predictions, facts=facts)

    positional_kinds = _outcomes_for(positional, "keyed", "line_items")
    keyed_kinds = _outcomes_for(keyed, "keyed", "line_items")

    assert positional_kinds["line_items[0].description"] is FieldOutcomeKind.INCORRECT
    assert positional_kinds["line_items[1].description"] is FieldOutcomeKind.INCORRECT
    assert keyed_kinds["line_items[0].description"] is FieldOutcomeKind.CORRECT
    assert keyed_kinds["line_items[1].description"] is FieldOutcomeKind.CORRECT

    assert positional.metrics.micro["field_accuracy"].numerator == 25
    assert keyed.metrics.micro["field_accuracy"].numerator == 27


def test_the_keyed_report_records_the_policy_and_the_key() -> None:
    """A number whose alignment policy is unrecorded cannot be compared to another."""
    report = evaluate(keyed_golden_set(), prediction_set(), facts=facts_for_fixtures())

    groups = [g for g in report.metrics.group_outcomes if g.group_path == "line_items"]
    assert groups, "the fixture has repeating groups; none were reported"
    for group in groups:
        assert group.alignment.policy == KEYED
        assert group.alignment.key_field == "description"


# -- keyed -------------------------------------------------------------------


def test_keyed_alignment_pairs_by_key_not_position() -> None:
    pairs, outcome = align(
        document_id="d",
        group_path="line_items",
        expected_keys=["A", "B"],
        predicted_keys=["B", "A"],
        key_field="sku",
    )

    assert pairs == [(0, 1), (1, 0)]
    assert outcome.alignment.policy == KEYED
    assert outcome.missing_entries == 0
    assert outcome.spurious_entries == 0


def test_an_unmatched_expected_entry_is_missing_and_an_unmatched_predicted_one_is_spurious() -> (
    None
):
    pairs, outcome = align(
        document_id="d",
        group_path="line_items",
        expected_keys=["A", "B"],
        predicted_keys=["B", "C"],
        key_field="sku",
    )

    assert (0, None) in pairs, "A was expected and not predicted"
    assert (None, 1) in pairs, "C was predicted and not expected"
    assert outcome.missing_entries == 1
    assert outcome.spurious_entries == 1


@pytest.mark.parametrize("side", ["expected", "predicted"])
def test_duplicate_key_values_within_one_side_are_refused(side: str) -> None:
    """Refused rather than resolved by a tie-break (EVA-13a).

    Any tie-break would be an invented rule deciding which of two
    identical-looking entries the truth was about -- and it would be applied
    silently, to a dataset whose author believed the key was unique.
    """
    keys = ["A", "A"]
    with pytest.raises(EvaluationError, match="repeat"):
        align(
            document_id="d",
            group_path="line_items",
            expected_keys=keys if side == "expected" else ["A", "B"],
            predicted_keys=keys if side == "predicted" else ["A", "B"],
            key_field="sku",
        )


def test_duplicate_expected_keys_are_refused_at_load() -> None:
    """The expected side is knowable before scoring, so it is refused before scoring.

    The predicted side cannot be: nothing at load has seen a prediction. Both are
    checked; only the timing differs, and the timing is what makes one an
    authoring error and the other a scoring failure.
    """
    import pathlib
    import tempfile

    from docdoc.evaluation import load_golden_set
    from docdoc.evaluation.golden import validate_golden_set
    from tests.fixtures.evaluation.authoring_errors import duplicate_key_values, write_dataset

    with tempfile.TemporaryDirectory() as raw:
        manifest = write_dataset(pathlib.Path(raw), duplicate_key_values())
        with pytest.raises(EvaluationError, match="twice"):
            load_golden_set(manifest, facts=facts_for_fixtures())

    # And the same set, once built, is refused by the validator directly -- the
    # check must not be reachable only through the file path.
    assert validate_golden_set(golden_set()) is None


# -- the count mismatch as its own fact (FR-021) -----------------------------


def test_an_entry_count_mismatch_is_reported_as_its_own_fact() -> None:
    """Four expected against five predicted, stated once rather than inferred twelve times."""
    _pairs, outcome = align(
        document_id="d",
        group_path="line_items",
        expected_keys=[0, 1, 2, 3],
        predicted_keys=[0, 1, 2, 3, 4],
        key_field=None,
    )

    assert not outcome.count_matches
    assert outcome.expected_entries == 4
    assert outcome.predicted_entries == 5
    assert outcome.spurious_entries == 1
    assert outcome.missing_entries == 0


def test_a_missing_entry_also_produces_the_field_outcomes_it_causes() -> None:
    """Both halves. FR-021 adds a fact; it does not remove the field-level ones.

    A report that only said "one entry short" would hide which fields were lost,
    and a report that only listed the fields would hide that there was one cause.
    """
    _pairs, outcome = align(
        document_id="d",
        group_path="line_items",
        expected_keys=[0, 1, 2],
        predicted_keys=[0, 1],
        key_field=None,
    )
    assert outcome.missing_entries == 1

    facts = facts_for_fixtures()
    report = evaluate(golden_set(), prediction_set(), facts=facts)

    # `near-miss` labels no entries and predicts none, so its group is absent
    # entirely -- a group nobody spoke about is not a group with zero entries.
    groups = {g.document_id for g in report.metrics.group_outcomes}
    assert "near-miss" not in groups
    assert {"clean", "keyed"} <= groups
