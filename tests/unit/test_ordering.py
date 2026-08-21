"""T018 — the report's order is total, and entry 2 precedes entry 10 (EVA-26).

FR-043 requires a report to be byte-identical on every run and every platform,
and "identical" only means something if the ordering is total. Two hazards make
that non-trivial, and both have bitten this repository before:

**Lexicographic ordering puts ``[10]`` before ``[2]``.** Milestone 5 hit exactly
this and solved it by carrying the entry index separately. The hazard is the same
here and the fix is the same in spirit: indices are typed as integers, not
compared as text.

**Set and dict iteration order varies with ``PYTHONHASHSEED``.** A single-seeded
suite passes a hash-order dependency by luck. The shuffle test below is the local
half of that check; ``test_report_determinism.py`` is the half that runs under two
seeds in CI.
"""

from __future__ import annotations

import random

from docdoc.evaluation.ordering import path_key
from docdoc.evaluation.score import outcome_sort_key
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import prediction_set


def test_entry_two_precedes_entry_ten() -> None:
    """The lexicographic trap, stated as the assertion it is.

    As text, ``"line_items[10]"`` sorts before ``"line_items[2]"``, because ``1``
    precedes ``2``. A report ordered that way is still deterministic -- and still
    wrong for every human reading it, and still different from the order every
    other layer of docdoc reports entries in.
    """
    assert path_key("line_items[2].amount") < path_key("line_items[10].amount")
    assert "line_items[10].amount" < "line_items[2].amount", (
        "if this ever stops being true the trap has gone away and so has the "
        "reason for path_key; the test would then be asserting nothing"
    )


def test_indices_decompose_as_integers() -> None:
    """The shape the ordering depends on, pinned so a refactor cannot quietly drop it."""
    assert path_key("line_items[10].amount") == (
        (1, "line_items", 0),
        (0, "", 10),
        (1, "amount", 0),
    )


def test_a_name_never_compares_against_an_index() -> None:
    """The discriminator keeps tuple element types uniform.

    Without the leading ``0``/``1``, Python would compare a ``str`` against an
    ``int`` at sort time and raise ``TypeError`` -- on some datasets and not
    others, depending on which paths happened to be adjacent.
    """
    assert path_key("line_items[0]") < path_key("line_items.total")


def test_the_order_is_total_over_the_fixture() -> None:
    """No two outcomes compare equal, so there is exactly one valid ordering."""
    from docdoc.evaluation import evaluate

    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    keys = [outcome_sort_key(outcome) for outcome in report.outcomes]

    assert len(keys) == len(set(keys)), (
        "two outcomes share a sort key, so their relative order is decided by "
        "whatever order they happened to be produced in -- which is the hash-seed "
        "dependency FR-043 forbids"
    )
    assert keys == sorted(keys), "the report emitted its outcomes out of order"


def test_the_order_is_unchanged_across_a_shuffled_input() -> None:
    """Sorting must depend on the outcome, never on arrival order."""
    from docdoc.evaluation import evaluate

    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    outcomes = list(report.outcomes)

    shuffled = outcomes[:]
    random.Random(20260820).shuffle(shuffled)
    assert shuffled != outcomes, "the shuffle must actually disturb the order"

    assert sorted(shuffled, key=outcome_sort_key) == outcomes


def test_the_order_leads_with_the_tier() -> None:
    """Public before restricted, so a partial run's report is a prefix of a full one.

    Not cosmetic: a reader diffing a public-only report against one that included
    the restricted tier sees an append rather than an interleave.
    """
    key = outcome_sort_key
    from docdoc.evaluation import FieldOutcome, FieldOutcomeKind, Tier

    public = FieldOutcome(
        document_id="z", field_path="a", kind=FieldOutcomeKind.CORRECT, tier=Tier.PUBLIC
    )
    restricted = FieldOutcome(
        document_id="a", field_path="a", kind=FieldOutcomeKind.CORRECT, tier=Tier.RESTRICTED
    )

    assert key(public) < key(restricted)
