"""T052 — five refusals naming both sides, and seventeen provenance fields (SC-010, SC-011).

A refusal that names one side is barely better than no refusal. "Schema mismatch"
sends the reader to work out which of two schemas was wrong, on a dataset they may
not have written, for a run they may not have started. Both sides, every time.

The positive half of this file matters as much as the negative half. FR-040 lists
seventeen things a report must record, and they are **enumerated explicitly here
rather than counted with ``len()``**. A test that asserted "seventeen fields"
stays green when somebody renames one, which is exactly the drift it exists to
catch: the count is right, the report is wrong, and the field a reader was
looking for is gone.
"""

from __future__ import annotations

import pytest

from docdoc.evaluation import (
    DocumentPrediction,
    EvaluationError,
    PredictionSet,
    evaluate,
)
from tests.fixtures.evaluation.datasets import facts_for_fixtures, golden_set
from tests.fixtures.evaluation.predictions import (
    MISMATCHED_SCHEMA,
    mismatched_prediction,
    prediction_for,
    prediction_set,
)

#: Every field FR-040 requires a report to record. Written out, not counted.
PROVENANCE_FIELDS = (
    "repo_revision",
    "golden_set_id",
    "prediction_set_id",
    "schema_identities",
    "schema_hashes",
    "prompt_hashes",
    "model_ids",
    "model_versions",
    "parser_ids",
    "parser_versions",
    "grounding_versions",
    "validator_versions",
    "scorer_id",
    "scorer_version",
    "metric_definition_version",
    "comparator_versions",
    "entry_alignment_version",
    "location_rule_version",
)


# -- refusal 1: a prediction for a document nobody labelled ------------------


def test_a_prediction_for_an_unknown_document_is_refused_naming_it() -> None:
    """FR-005. The two sides do not describe the same thing."""
    predictions = prediction_set()
    stray = predictions.model_copy(
        update={
            "predictions": {
                **predictions.predictions,
                "not-in-the-golden-set": prediction_for("clean"),
            }
        }
    )

    with pytest.raises(EvaluationError) as raised:
        evaluate(golden_set(), stray, facts=facts_for_fixtures())

    assert "not-in-the-golden-set" in str(raised.value)
    assert raised.value.document_id == "not-in-the-golden-set"


# -- refusals 2 and 3: the schema does not match -----------------------------


def test_a_differing_schema_identity_is_refused_naming_both_sides() -> None:
    """ADR-0008: a label written under ``invoice@1`` says nothing about ``invoice@2``.

    A major bump means a consumer contract broke, so the two results are not
    describing the same fields -- and a number computed across them describes
    neither.
    """
    predictions = prediction_set()
    mismatched = predictions.model_copy(
        update={"predictions": {**predictions.predictions, "clean": mismatched_prediction()}}
    )

    with pytest.raises(EvaluationError) as raised:
        evaluate(golden_set(), mismatched, facts=facts_for_fixtures())

    message = str(raised.value)
    assert "invoice@1" in message, f"the refusal does not name the labelled side: {message}"
    assert MISMATCHED_SCHEMA in message, f"the refusal does not name the predicted side: {message}"
    assert raised.value.expected == "invoice@1"
    assert raised.value.actual == MISMATCHED_SCHEMA


def test_a_differing_schema_hash_is_refused_naming_both_sides() -> None:
    """A result-affecting schema edit invalidates the labels written against it.

    The identity is unchanged here, so nothing warns a reader: same name, same
    version, different rules. The hash is the only thing that can tell.
    """
    golden = golden_set()
    tampered = golden.model_copy(
        update={
            "documents": tuple(
                d.model_copy(update={"schema_hash": "sha256:" + "0" * 64})
                if d.document_id == "clean"
                else d
                for d in golden.documents
            )
        }
    )

    with pytest.raises(EvaluationError) as raised:
        evaluate(tampered, prediction_set(), facts=facts_for_fixtures())

    assert raised.value.expected == "sha256:" + "0" * 64
    assert raised.value.actual
    assert raised.value.actual != raised.value.expected
    assert raised.value.expected in str(raised.value)
    assert raised.value.actual in str(raised.value)


# -- refusal 4: a provenance field it cannot record --------------------------


def test_a_run_that_cannot_record_a_provenance_field_is_refused() -> None:
    """FR-041. Recording a null would be the vague claim wearing a field name.

    A null in ``model_id`` reads as "this run had no model", which is a claim,
    and a false one. Refusing says the true thing: this cannot be reported.
    """
    from docdoc.evaluation.report import EvaluationProvenance

    with pytest.raises(EvaluationError) as raised:
        evaluate(golden_set(), prediction_set(), facts=facts_for_fixtures(), repo_revision="")

    assert "repo_revision" in str(raised.value)
    assert raised.value.field_path == "repo_revision"
    assert issubclass(EvaluationProvenance, object)


def test_a_run_over_documents_that_records_no_schema_is_refused() -> None:
    """The list-valued half of FR-041, and the one an empty dataset must not trip.

    "No schemas" is a true answer for a run over nothing and a lost fact for a
    run over something. The refusal distinguishes them by asking how many
    documents were scored.
    """
    from docdoc.evaluation.report import EvaluationOptions, EvaluationProvenance

    provenance = EvaluationProvenance(
        repo_revision="abc",
        golden_set_id="sha256:x",
        prediction_set_id="sha256:y",
        schema_identities=(),
        schema_hashes=(),
        prompt_hashes=(),
        model_ids=(),
        model_versions=(),
        parser_ids=(),
        parser_versions=(),
        grounding_versions=(),
        validator_versions=(),
        scorer_id="golden-set-scorer",
        scorer_version="1.0.0",
        metric_definition_version="metric_definitions@1",
        comparator_versions={},
        entry_alignment_version="positional@1",
        location_rule_version="page_box@1",
        options=EvaluationOptions(),
    )

    provenance.refuse_if_incomplete(documents_considered=0)

    with pytest.raises(EvaluationError, match="schema_identities"):
        provenance.refuse_if_incomplete(documents_considered=6)


# -- refusal 5: a restricted bundle short of its declaration -----------------


def test_a_restricted_bundle_disagreeing_with_the_manifest_is_refused(tmp_path) -> None:  # type: ignore[no-untyped-def]
    """EVA-5a. A bundle short of its declaration is a smaller denominator in disguise.

    It would produce a full-looking report over a fraction of the restricted
    tier, with nothing anywhere saying so.
    """
    import json

    from docdoc.evaluation import load_golden_set
    from tests.fixtures.evaluation.datasets import INVOICE, registry

    blob = "sha256:" + "b" * 64
    manifest = {
        "documents": [
            {
                "document_id": "restricted-one",
                "blob_sha256": blob,
                "tier": "restricted",
                "origin": {"kind": "restricted", "basis": "Held by the corpus owner."},
                "schema_identity": INVOICE,
                "schema_hash": registry().resolve(INVOICE).schema_hash,
                "declared_label_count": 3,
            }
        ]
    }
    (tmp_path / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    bundle = tmp_path / "bundle.json"
    bundle.write_text(
        json.dumps({blob: [{"field_path": "total", "expectation": "value", "value": "1.00"}]}),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationError) as raised:
        load_golden_set(
            tmp_path / "manifest.json",
            restricted_bundle=bundle,
            facts=facts_for_fixtures(),
        )

    assert raised.value.expected == "3"
    assert raised.value.actual == "1"
    assert "restricted-one" in str(raised.value)


# -- the positive half: all seventeen provenance fields ----------------------


def test_a_successful_report_records_every_provenance_field() -> None:
    """FR-040, enumerated rather than counted.

    ``len(fields) == 17`` would pass after a rename, which is the failure this
    guards: the count stays right and the field a reader was told to look for is
    gone.
    """
    report = evaluate(
        golden_set(), prediction_set(), facts=facts_for_fixtures(), repo_revision="abc123"
    )
    provenance = report.provenance

    missing = [name for name in PROVENANCE_FIELDS if not hasattr(provenance, name)]
    assert not missing, f"the report's provenance no longer carries {missing}"

    empty = [name for name in PROVENANCE_FIELDS if not getattr(provenance, name)]
    assert not empty, (
        f"these provenance fields are recorded but empty on a successful run: {empty}. "
        "An empty field is the null FR-041 refuses, one indirection away"
    )


def test_the_enumerated_list_matches_the_model() -> None:
    """Keeps the hand-written list above honest in the other direction.

    A list of names to check goes stale exactly when nobody is looking at it, so
    this asserts the model has no provenance field the list forgot.
    """
    from docdoc.evaluation.report import EvaluationProvenance

    declared = set(EvaluationProvenance.model_fields)
    # `options` carries the option object itself rather than one FR-040 item.
    assert declared - {"options"} == set(PROVENANCE_FIELDS), (
        f"the model and this file's list have diverged: "
        f"model-only={sorted(declared - {'options'} - set(PROVENANCE_FIELDS))}, "
        f"list-only={sorted(set(PROVENANCE_FIELDS) - declared)}"
    )


def test_every_refusal_carries_both_sides_as_attributes() -> None:
    """contracts §8: a caller that has to parse prose to learn which side was wrong will not."""
    predictions = prediction_set()
    mismatched = predictions.model_copy(
        update={"predictions": {**predictions.predictions, "clean": mismatched_prediction()}}
    )

    with pytest.raises(EvaluationError) as raised:
        evaluate(golden_set(), mismatched, facts=facts_for_fixtures())

    assert raised.value.expected is not None
    assert raised.value.actual is not None
    assert raised.value.document_id is not None


def test_a_document_with_no_prediction_is_not_refused() -> None:
    """The asymmetry is deliberate and load-bearing (FR-005).

    A prediction for an unknown document is refused; a golden-set document with
    no prediction is **reported**, because dropping it is how a crash becomes an
    accuracy improvement.
    """
    report = evaluate(
        golden_set(),
        PredictionSet(predictions={"clean": prediction_for("clean")}),
        facts=facts_for_fixtures(),
    )

    unevaluated = {o.document_id for o in report.outcomes if str(o.kind) == "unevaluated"}
    assert unevaluated == {"near-miss", "keyed", "receipt", "failing", "silent"}


def test_an_empty_prediction_is_not_a_missing_one() -> None:
    """A recorded prediction that found nothing is a result, not an absence."""
    empty = DocumentPrediction(document_id="clean")
    report = evaluate(
        golden_set(), PredictionSet(predictions={"clean": empty}), facts=facts_for_fixtures()
    )

    clean = next(s for s in report.document_scores if s.document_id == "clean")
    assert clean.evaluated, "a prediction exists, so the document was evaluated"
    assert clean.counts.missing == 9, "its nine value labels found nothing"
    assert clean.counts.correct_absence == 2, "and its two absence labels are satisfied"


# -- FR-060's third field: the dataset at fault (T096) -----------------------
#
# `EvaluationError` has carried `dataset` since the first version and no raise
# site populated it, so it was always `None`. That is worse than an absent
# attribute: a caller reading it got a confident "unknown" for a dataset that was
# perfectly well known, and no test noticed because `None` is a legal value.
#
# The label is attached at the entry points rather than at the raise sites. A
# comparator refusing a duplicate alignment key knows the group and the entry and
# has never been told which golden set it is scoring; threading an identity
# through every helper to satisfy one attribute would put an unread parameter into
# a dozen signatures, and the first person to add a check would forget it.


def test_a_scoring_refusal_names_the_dataset() -> None:
    """The identity `evaluate()` computed, not the empty one the set carries.

    A `GoldenSet` built in memory has no `golden_set_id` of its own, so falling
    back to it would have left this `None` for every caller who did not load from
    disk — which is most of the test suite and every library user.
    """
    predictions = prediction_set()
    stray = predictions.model_copy(
        update={"predictions": {**predictions.predictions, "ghost": prediction_for("clean")}}
    )

    with pytest.raises(EvaluationError) as raised:
        evaluate(golden_set(), stray, facts=facts_for_fixtures())

    assert raised.value.dataset is not None
    assert raised.value.dataset.startswith("sha256:")


def test_the_dataset_named_is_the_one_being_scored() -> None:
    """Not merely non-empty — correct."""
    from docdoc.evaluation.identity import golden_set_id_for

    golden = golden_set()
    predictions = prediction_set()
    stray = predictions.model_copy(
        update={"predictions": {**predictions.predictions, "ghost": prediction_for("clean")}}
    )

    with pytest.raises(EvaluationError) as raised:
        evaluate(golden, stray, facts=facts_for_fixtures())

    assert raised.value.dataset == golden_set_id_for(golden)


def test_a_refusal_carries_all_three_of_fr_060s_fields() -> None:
    """The dataset, the document, and the field or label at fault."""
    predictions = prediction_set()
    mismatched = predictions.model_copy(
        update={"predictions": {**predictions.predictions, "clean": mismatched_prediction()}}
    )

    with pytest.raises(EvaluationError) as raised:
        evaluate(golden_set(), mismatched, facts=facts_for_fixtures())

    error = raised.value
    assert error.dataset
    assert error.document_id == "clean"
    assert error.expected
    assert error.actual


def test_the_innermost_label_wins() -> None:
    """`compare()` has two datasets in scope and the refusal already named one.

    An outer entry point overwriting it would replace the right answer with a
    merely-adjacent one, which is the failure mode a blanket label invites.
    """
    from docdoc.evaluation.errors import naming

    with pytest.raises(EvaluationError) as raised, naming("sha256:outer"):
        raise EvaluationError("already labelled", dataset="sha256:inner")

    assert raised.value.dataset == "sha256:inner"


def test_nothing_known_stays_none() -> None:
    """A label of `None` must not become the string "None"."""
    from docdoc.evaluation.errors import naming

    with pytest.raises(EvaluationError) as raised, naming(None):
        raise EvaluationError("nothing is known about the dataset")

    assert raised.value.dataset is None
