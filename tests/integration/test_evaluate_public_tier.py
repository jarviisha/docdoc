"""T076 — the whole path, end to end, from a checkout alone (US3, SC-001, SC-022).

Everything above this file tests a piece. This runs what a first-time contributor
runs: load the committed manifest, load the committed predictions, score them,
and get real numbers over a real golden set.

**No credentials, no network, no provider, no database, no object store.** SC-022
says a contributor runs 100% of this feature's tests and 100% of its documented
examples, and a test that skipped would be the first thing to make that false. So
this one does not skip — there is no marker, no environment check, and no
condition under which it declines to run.
"""

from __future__ import annotations

import json

from tests.fixtures.evaluation.datasets import MVP_ROOT

from docdoc.evaluation import (
    FieldOutcomeKind,
    Tier,
    evaluate,
    load_golden_set,
    load_prediction_set,
    schema_facts,
)
from docdoc.extraction import SchemaRegistry


def _facts():  # type: ignore[no-untyped-def]
    registry = SchemaRegistry.from_paths(["schemas"])
    return schema_facts([registry.describe(identity) for identity in registry.identities()])


def test_the_committed_tier_scores_end_to_end() -> None:
    """The contributor's path, spelled out rather than helped along by fixtures."""
    facts = _facts()
    golden = load_golden_set(MVP_ROOT / "manifest.json", facts=facts)
    predictions = load_prediction_set(MVP_ROOT / "predictions", facts=facts)

    report = evaluate(golden, predictions, facts=facts)

    assert report.report_id.startswith("sha256:")
    assert report.metrics.micro["field_accuracy"].value is not None
    assert report.metrics.counts.labelled == 28


def test_the_dataset_spans_two_schemas() -> None:
    """FR-013. A dataset that could only describe invoices would reintroduce in
    data the coupling Principle VI forbids in code."""
    golden = load_golden_set(MVP_ROOT / "manifest.json", facts=_facts())

    identities = {document.schema_identity for document in golden.documents}
    assert identities == {"invoice@1", "receipt@1"}


def test_the_numbers_are_the_ones_the_dataset_was_built_to_produce() -> None:
    """Pinned, because a dataset that scores 100% measures nothing.

    Two deliberate failures are authored into ``make_dataset.py``: a printed tax
    id the model does not read (MISSING) and a transposed total, 12.05 for 12.50
    (INCORRECT). If either stops being reported, the dataset has drifted into
    agreeing with the pipeline and has stopped being evidence.
    """
    facts = _facts()
    report = evaluate(
        load_golden_set(MVP_ROOT / "manifest.json", facts=facts),
        load_prediction_set(MVP_ROOT / "predictions", facts=facts),
        facts=facts,
    )

    accuracy = report.metrics.micro["field_accuracy"]
    assert (accuracy.numerator, accuracy.denominator) == (26, 28)

    kinds = {
        (o.document_id, o.field_path): o.kind
        for o in report.outcomes
        if o.kind not in (FieldOutcomeKind.CORRECT, FieldOutcomeKind.UNLABELED)
    }
    assert kinds == {
        ("invoice-002", "supplier.tax_id"): FieldOutcomeKind.MISSING,
        ("receipt-001", "total"): FieldOutcomeKind.INCORRECT,
    }


def test_the_incorrect_value_is_diagnosable_from_the_report_alone() -> None:
    """FR-026: a near-miss must be explainable without re-running anything."""
    facts = _facts()
    report = evaluate(
        load_golden_set(MVP_ROOT / "manifest.json", facts=facts),
        load_prediction_set(MVP_ROOT / "predictions", facts=facts),
        facts=facts,
    )

    total = next(
        o for o in report.outcomes if o.document_id == "receipt-001" and o.field_path == "total"
    )

    assert total.expected == "12.50"
    assert total.predicted == "12.05"
    assert total.comparator_version == "exact@1"


def test_the_run_is_partial_and_says_which_tier_it_skipped() -> None:
    facts = _facts()
    report = evaluate(
        load_golden_set(MVP_ROOT / "manifest.json", facts=facts),
        load_prediction_set(MVP_ROOT / "predictions", facts=facts),
        facts=facts,
    )

    assert report.partial is not None
    assert report.partial.skipped_tiers == (Tier.RESTRICTED,)
    assert report.partial.covered_labels == 28
    assert report.partial.declared_labels == 48


def test_the_manifest_states_its_size_per_tier_and_the_gate_target() -> None:
    """FR-009 and the honesty clause of ADR-0009.

    The distance to quality gate 5's target is a number a reader can see rather
    than a gap nobody mentions. Merging the tiers would make the gate unreadable:
    it counts the public tier alone, because CI cannot see the other one.
    """
    manifest = json.loads((MVP_ROOT / "manifest.json").read_text(encoding="utf-8"))
    size = manifest["size"]

    assert size["public"] == {"documents": 4, "labelled_fields": 28}
    assert size["restricted"] == {"documents": 2, "labelled_fields": 20}
    assert size["gate_5_target"] == {"documents": 50, "labelled_fields": 500}
    assert "total" not in size, "the tiers must not be merged (FR-009)"


def test_every_document_records_the_basis_on_which_docdoc_may_use_it() -> None:
    """FR-011, over the dataset that ships rather than over a fixture."""
    golden = load_golden_set(MVP_ROOT / "manifest.json", facts=_facts())

    for document in golden.documents:
        assert document.origin.basis.strip(), document.document_id
        if str(document.origin.kind) == "synthetic":
            assert document.origin.generator_id
            assert document.origin.generator_version


def test_the_predictions_replay_with_their_types_intact() -> None:
    """The quiet failure this would otherwise be.

    ``Decimal`` and the date types serialize to strings, and `comparators@1` gates
    on type identity — so a replay that returned strings would mark every decimal
    in the dataset incorrect and look exactly like a model that cannot read
    numbers.
    """
    from decimal import Decimal

    predictions = load_prediction_set(MVP_ROOT / "predictions", facts=_facts())
    invoice = predictions.for_document("invoice-001")
    assert invoice is not None
    assert invoice.extraction is not None

    total = invoice.extraction.values["total"]
    assert type(total.value) is Decimal
    assert total.value == Decimal("1240.00")


def test_scoring_writes_nothing_to_the_dataset() -> None:
    """FR-006 over the committed tier, which is the copy that would be committed back."""
    import hashlib

    before = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(MVP_ROOT.rglob("*"))
        if path.is_file()
    }

    facts = _facts()
    evaluate(
        load_golden_set(MVP_ROOT / "manifest.json", facts=facts),
        load_prediction_set(MVP_ROOT / "predictions", facts=facts),
        facts=facts,
    )

    after = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(MVP_ROOT.rglob("*"))
        if path.is_file()
    }
    assert before == after
