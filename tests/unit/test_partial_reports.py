"""T071 — a partial run says so, and states exactly what it missed (FR-015, SC-016).

The failure this prevents: a contributor without the restricted corpus runs the
evaluation, gets a number over the public tier alone, and reports it as *the*
accuracy. Nothing in the output would be wrong, and nothing would be right
either — the number describes an unannounced subset, and the reader has no way
to know which one.

**The covered fraction is exact integers, not an estimate**, and that is what
``declared_label_count`` on every document exists for (EVA-5a). The manifest
commits how many labels each restricted document holds even though it cannot
commit the labels themselves. Without it a checkout could count restricted
*documents* and would have to guess at their labels — so a partial report would
be estimating its own denominator, which is a worse version of the problem it is
there to solve.

FR-001 forbids a partial report that is not marked partial, which is why
``partial`` has been a field on the model since the first version rather than
something a caller infers: added later, every earlier report would have silently
claimed to be complete.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from docdoc.evaluation import (
    EvaluationError,
    EvaluationOptions,
    Tier,
    compare,
    evaluate,
    load_golden_set,
)
from tests.fixtures.evaluation.datasets import (
    MVP_ROOT,
    RESTRICTED_LABEL_COUNT,
    committed_golden_set,
    committed_prediction_set,
    facts_for_fixtures,
    golden_set,
)
from tests.fixtures.evaluation.predictions import prediction_set

FACTS = facts_for_fixtures()


def test_a_run_without_the_restricted_bundle_is_marked_partial() -> None:
    report = evaluate(golden_set(), prediction_set(), facts=FACTS)

    assert report.partial is not None, (
        "the restricted tier was skipped and the report does not say so, which is "
        "exactly the claim FR-001 forbids"
    )


def test_it_names_the_documents_and_the_tiers_it_skipped() -> None:
    """Naming them is what makes the gap actionable rather than merely disclosed."""
    report = evaluate(golden_set(), prediction_set(), facts=FACTS)
    partial = report.partial
    assert partial is not None

    assert partial.skipped_documents == ("restricted-invoice",)
    assert partial.skipped_tiers == (Tier.RESTRICTED,)


def test_the_covered_fraction_is_exact_integers() -> None:
    """36 of 40 labels, read from the manifest's committed counts (EVA-27a)."""
    report = evaluate(golden_set(), prediction_set(), facts=FACTS)
    partial = report.partial
    assert partial is not None

    assert partial.covered_documents == 6
    assert partial.declared_documents == 7
    assert partial.covered_labels == 36
    assert partial.declared_labels == 36 + RESTRICTED_LABEL_COUNT
    assert partial.covered_fraction == pytest.approx(36 / 40)


def test_the_declared_count_comes_from_the_manifest_not_from_the_labels() -> None:
    """The restricted document carries a count and no labels, which is the point.

    If the count were derived from the labels present, a run without the bundle
    would compute 36 of 36 and report itself complete — the exact failure EVA-5a
    exists to prevent, arriving through arithmetic rather than through omission.
    """
    golden = golden_set()
    restricted = golden.document("restricted-invoice")
    assert restricted is not None

    assert golden.labels_for("restricted-invoice") == ()
    assert restricted.declared_label_count == RESTRICTED_LABEL_COUNT


def test_a_complete_run_is_not_marked_partial() -> None:
    """``None`` rather than a declaration of nothing.

    A partial declaration that was always present would train readers to ignore
    it, which is the same as not having it.
    """
    report = evaluate(
        golden_set(),
        prediction_set(include_restricted=True),
        facts=FACTS,
        options=EvaluationOptions(include_restricted=True),
    )

    assert report.partial is None


def test_a_partial_report_cannot_be_compared_against_a_full_one() -> None:
    """The smaller number is not worse, it is less (FR-046, EVA-28a)."""
    partial = evaluate(golden_set(), prediction_set(), facts=FACTS)
    full = evaluate(
        golden_set(),
        prediction_set(include_restricted=True),
        facts=FACTS,
        options=EvaluationOptions(include_restricted=True),
    )

    with pytest.raises(EvaluationError, match="partial"):
        compare(partial, full)
    with pytest.raises(EvaluationError, match="partial"):
        compare(full, partial)


def test_two_partial_reports_compare_normally() -> None:
    """The refusal is about the mismatch, not about partiality itself.

    A team without the restricted corpus must still be able to detect their own
    regressions, or the feature is unusable for exactly the contributors ADR-0009
    designed the two-tier split for.
    """
    first = evaluate(golden_set(), prediction_set(), facts=FACTS)
    second = evaluate(golden_set(), prediction_set(), facts=FACTS)

    delta = compare(first, second)

    assert delta.metrics["field_accuracy"].delta == 0


def test_the_committed_dataset_reports_its_own_partiality() -> None:
    """End to end over ``datasets/mvp/``, which is what a contributor runs."""
    report = evaluate(committed_golden_set(), committed_prediction_set(), facts=FACTS)
    partial = report.partial
    assert partial is not None

    assert partial.skipped_tiers == (Tier.RESTRICTED,)
    assert partial.covered_documents == 4
    assert partial.declared_documents == 6
    assert partial.covered_labels == 28
    assert partial.declared_labels == 48


def test_a_restricted_bundle_resolves_by_content_hash(tmp_path: Path) -> None:
    """The restricted tier is referenced by hash because it is not in the tree.

    A ``document_id`` would work until somebody renamed one; the content hash is
    the only thing the manifest can state about a document it does not carry.
    """
    manifest = json.loads((MVP_ROOT / "manifest.json").read_text(encoding="utf-8"))
    restricted = next(d for d in manifest["documents"] if d["tier"] == "restricted")

    bundle = tmp_path / "restricted.json"
    bundle.write_text(
        json.dumps(
            {
                restricted["blob_sha256"]: [
                    {"field_path": "invoice_number", "expectation": "value", "value": f"R-{index}"}
                    for index in range(restricted["declared_label_count"])
                ]
            }
        ),
        encoding="utf-8",
    )

    # Eleven labels all addressing one path is a duplicate-path authoring error,
    # so the bundle is refused -- which is itself the check working. The
    # resolution by hash is what got far enough to produce that message.
    with pytest.raises(EvaluationError) as raised:
        load_golden_set(MVP_ROOT / "manifest.json", restricted_bundle=bundle, facts=FACTS)

    assert raised.value.document_id == restricted["document_id"], (
        "the bundle was keyed by blob hash and the error names the document it "
        "resolved to, which is the resolution EVA-5a describes"
    )


def test_a_bundle_naming_an_unknown_blob_is_refused(tmp_path: Path) -> None:
    """Two sides describing different corpora. Scoring one against the other
    would report a number about neither."""
    bundle = tmp_path / "restricted.json"
    bundle.write_text(json.dumps({"sha256:" + "9" * 64: []}), encoding="utf-8")

    with pytest.raises(EvaluationError, match="does not contain"):
        load_golden_set(MVP_ROOT / "manifest.json", restricted_bundle=bundle, facts=FACTS)


def test_a_bundle_short_of_its_declaration_is_refused(tmp_path: Path) -> None:
    """A smaller denominator wearing a full report's clothes (EVA-5a)."""
    manifest = json.loads((MVP_ROOT / "manifest.json").read_text(encoding="utf-8"))
    restricted = next(d for d in manifest["documents"] if d["tier"] == "restricted")

    bundle = tmp_path / "restricted.json"
    bundle.write_text(
        json.dumps(
            {
                restricted["blob_sha256"]: [
                    {"field_path": "total", "expectation": "value", "value": "1.00"}
                ]
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(EvaluationError) as raised:
        load_golden_set(MVP_ROOT / "manifest.json", restricted_bundle=bundle, facts=FACTS)

    assert raised.value.expected == str(restricted["declared_label_count"])
    assert raised.value.actual == "1"


# -- the dataset's size, on every report (T095, FR-009) ----------------------
#
# FR-009's justification is the test of this requirement: the constitution's fifth
# quality gate turns blocking at a target size, and *a target nobody can read off
# a report is a target nobody can apply*. Until now the counts existed on
# `GoldenSet.tier_counts()` and reached only the example, so a complete run
# reported no size at all.


def test_every_report_carries_the_dataset_size_per_tier() -> None:
    """Complete or partial, the size is there."""
    report = evaluate(committed_golden_set(), committed_prediction_set(), facts=FACTS)

    sizes = {
        str(entry.tier): (entry.documents, entry.labelled_fields) for entry in report.dataset_size
    }
    assert sizes == {"public": (4, 28), "restricted": (2, 20)}


def test_a_complete_run_still_carries_it() -> None:
    """The case that had nothing before.

    A partial run at least stated its covered fraction; a complete one stated no
    size whatsoever, which is the reader FR-009 is written for.
    """
    report = evaluate(
        golden_set(),
        prediction_set(include_restricted=True),
        facts=FACTS,
        options=EvaluationOptions(include_restricted=True),
    )

    assert report.partial is None
    assert report.dataset_size, "a complete report must still say how big the dataset was"


def test_the_tiers_are_never_merged() -> None:
    """Gate 5 counts the public tier alone, because CI cannot see the other one.

    A merged total would make the gate unreadable — 6 documents and 48 fields
    against a target of 50 and 500 says something quite different from 4 and 28.
    """
    report = evaluate(committed_golden_set(), committed_prediction_set(), facts=FACTS)

    tiers = [str(entry.tier) for entry in report.dataset_size]
    assert tiers == sorted(tiers), "reported in a stable order"
    assert len(tiers) == len(set(tiers)), "one entry per tier, never a total row"
    assert not any(str(entry.tier) in {"total", "all"} for entry in report.dataset_size)


def test_the_size_describes_the_dataset_and_the_partial_declaration_the_run() -> None:
    """Two different facts, and conflating them loses one.

    `dataset_size` includes the tier the run skipped; `partial` says what the run
    covered. Without both, a reader cannot tell a small dataset from a large one
    measured narrowly.
    """
    report = evaluate(committed_golden_set(), committed_prediction_set(), facts=FACTS)
    partial = report.partial
    assert partial is not None

    declared = sum(entry.labelled_fields for entry in report.dataset_size)
    assert declared == partial.declared_labels == 48
    assert partial.covered_labels == 28
    assert any(str(entry.tier) == "restricted" for entry in report.dataset_size), (
        "the skipped tier is part of the dataset and must still be sized"
    )


def test_the_size_counts_restricted_labels_it_does_not_have() -> None:
    """`declared_label_count` is exactly what makes this possible (EVA-5a)."""
    golden = committed_golden_set()
    restricted = [d for d in golden.documents if str(d.tier) == "restricted"]

    assert all(golden.labels_for(d.document_id) == () for d in restricted)
    assert sum(d.declared_label_count for d in restricted) == 20


def test_the_size_does_not_move_the_report_id() -> None:
    """It is a function of the golden set, which `golden_set_id` already covers.

    Folding it into the identity again would move every committed report's id
    without any measurement having changed — the over-sensitivity FR-042's second
    half forbids.
    """
    report = evaluate(committed_golden_set(), committed_prediction_set(), facts=FACTS)

    assert report.dataset_size
    assert report.report_id == (
        "sha256:c3b4bc8e686aaa25a710067857b6aff7dc90f20c679057cba229b992d88d2b8e"
    )
