"""T058 — two runs produce identical bytes and identical ids (FR-043, SC-009).

Scoring must be deterministic even though the thing it scores was not. That is
what makes a report citable: a number somebody else cannot reproduce is an
anecdote, and a ``report_id`` that moved between runs would refuse every
comparison for no reason.

**Run this under two hash seeds.** ``PYTHONHASHSEED`` randomises string hashing,
and a dict- or set-iteration dependency is invisible under a single seed -- it
passes on your machine and fails on a colleague's, or in CI next Tuesday.
``.github/workflows/ci.yml`` already pins two seeds for Milestone 4, and
quickstart Scenario 5 gives the two commands:

    PYTHONHASHSEED=0     uv run pytest tests/property/test_report_determinism.py -q
    PYTHONHASHSEED=12345 uv run pytest tests/property/test_report_determinism.py -q

The subprocess test at the bottom does not wait for CI to run both: it re-runs
the scorer in a fresh interpreter under a different seed and compares the bytes.
"""

from __future__ import annotations

import json
import subprocess
import sys
import textwrap

from hypothesis import given, settings
from hypothesis import strategies as st

from docdoc.evaluation import EvaluationOptions, evaluate
from tests.fixtures.evaluation.datasets import (
    facts_for_fixtures,
    golden_set,
    keyed_golden_set,
)
from tests.fixtures.evaluation.predictions import prediction_set

#: Emitted by the subprocess below and compared across seeds.
_PROBE = textwrap.dedent(
    """
    import json, sys
    sys.path.insert(0, ".")
    from docdoc.evaluation import evaluate
    from tests.fixtures.evaluation.datasets import golden_set, facts_for_fixtures
    from tests.fixtures.evaluation.predictions import prediction_set

    report = evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures())
    print(json.dumps({"id": report.report_id, "body": report.model_dump_json()}))
    """
)


def test_two_runs_in_one_process_are_byte_identical() -> None:
    """The weakest form, and the one that catches an unsorted iteration outright."""
    golden = golden_set()
    predictions = prediction_set()
    facts = facts_for_fixtures()

    first = evaluate(golden, predictions, facts=facts)
    second = evaluate(golden, predictions, facts=facts)

    assert first.model_dump_json() == second.model_dump_json()
    assert first.report_id == second.report_id


def test_the_outcome_order_does_not_depend_on_input_order() -> None:
    """Reordering the predictions must not reorder the report.

    A dict preserves insertion order, so a scorer that emitted outcomes in
    prediction order would look deterministic in every single-process test and
    change the moment a dataset was rebuilt in a different order.
    """
    golden = golden_set()
    facts = facts_for_fixtures()
    predictions = prediction_set()

    reversed_predictions = predictions.model_copy(
        update={"predictions": dict(reversed(list(predictions.predictions.items())))}
    )

    first = evaluate(golden, predictions, facts=facts)
    second = evaluate(golden, reversed_predictions, facts=facts)

    assert [o.field_path for o in first.outcomes] == [o.field_path for o in second.outcomes]
    assert first.model_dump_json() == second.model_dump_json()
    assert first.report_id == second.report_id


def test_the_document_order_does_not_depend_on_manifest_order() -> None:
    """The same argument for the golden set.

    ``load_golden_set`` sorts documents on the way in, so this asserts the
    property rather than the implementation: a manifest edited by hand, with a
    new document appended, must produce the same report.
    """
    golden = golden_set()
    facts = facts_for_fixtures()
    predictions = prediction_set()

    shuffled = golden.model_copy(update={"documents": tuple(reversed(golden.documents))})

    assert evaluate(golden, predictions, facts=facts).model_dump_json() == (
        evaluate(shuffled, predictions, facts=facts).model_dump_json()
    )


@settings(max_examples=50, deadline=None)
@given(st.permutations(list(range(6))))
def test_any_permutation_of_the_inputs_produces_one_report(order: list[int]) -> None:
    """Swept over permutations rather than the two the tests above happen to try."""
    golden = golden_set()
    facts = facts_for_fixtures()
    predictions = prediction_set()

    items = list(predictions.predictions.items())
    permuted = predictions.model_copy(
        update={"predictions": {items[i][0]: items[i][1] for i in order if i < len(items)}}
    )
    baseline = predictions.model_copy(update={"predictions": dict(items)})

    assert evaluate(golden, permuted, facts=facts).report_id == (
        evaluate(golden, baseline, facts=facts).report_id
    )


def test_a_second_interpreter_under_a_different_hash_seed_agrees() -> None:
    """The check CI's two seeds exist for, run here rather than deferred.

    A fresh interpreter with ``PYTHONHASHSEED`` set differently is the only way
    to see a hash-order dependency from inside a test: the seed is fixed when the
    process starts, so no amount of in-process shuffling reaches it.
    """
    import os

    results = []
    for seed in ("0", "12345"):
        environment = {**os.environ, "PYTHONHASHSEED": seed}
        completed = subprocess.run(
            [sys.executable, "-c", _PROBE],
            capture_output=True,
            text=True,
            env=environment,
            check=False,
        )
        assert completed.returncode == 0, (
            f"the probe failed under PYTHONHASHSEED={seed}:\n{completed.stderr}"
        )
        results.append(json.loads(completed.stdout.strip().splitlines()[-1]))

    assert results[0]["id"] == results[1]["id"], (
        "the report id differs between hash seeds, which means something in the "
        "identity depends on set or dict iteration order"
    )
    assert results[0]["body"] == results[1]["body"], "the report bytes differ between hash seeds"


def test_the_alignment_policy_changes_the_report_and_stays_deterministic() -> None:
    """Determinism must not be achieved by ignoring an input.

    The keyed dataset genuinely produces a different report; both are stable.
    """
    facts = facts_for_fixtures()
    predictions = prediction_set()

    positional = evaluate(golden_set(), predictions, facts=facts)
    keyed = evaluate(keyed_golden_set(), predictions, facts=facts)

    assert positional.report_id != keyed.report_id
    assert (
        keyed.model_dump_json()
        == evaluate(keyed_golden_set(), predictions, facts=facts).model_dump_json()
    )


def test_options_are_folded_into_the_report_deterministically() -> None:
    """Same options, same id; different options, different id."""
    golden = golden_set()
    predictions = prediction_set()
    facts = facts_for_fixtures()

    default = evaluate(golden, predictions, facts=facts, options=EvaluationOptions())
    same = evaluate(golden, predictions, facts=facts, options=EvaluationOptions())
    restricted = evaluate(
        golden,
        predictions,
        facts=facts,
        options=EvaluationOptions(include_restricted=True),
    )

    assert default.report_id == same.report_id
    assert default.report_id != restricted.report_id
