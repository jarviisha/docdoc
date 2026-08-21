"""T088 — scoring a target-size dataset stays fast enough to run per commit.

Two bounds, derived in research.md R13 rather than asserted:

- **< 50 ms to score**, excluding load. 500 labelled fields at the measured cost
  of a comparison plus a cached path key is well inside this.
- **< 500 ms end to end**, including reading the dataset and the predictions
  from disk.

The gap between them is the interesting one. If load ever dominates past the
outer bound, **the fix is a leaner on-disk prediction form, not a relaxed bound**
— a scorer that takes half a second to start is a scorer nobody runs in a
pre-commit hook, and a quality mechanism nobody runs is not one.

Targets sit well above measurements, for the reason Milestones 2, 3, 4, and 5 all
recorded: a perf test that trips on machine noise gets disabled, and a disabled
test protects nothing. What these catch is a real regression in shape — a path
key recomputed per comparison instead of cached, an aggregation that went
quadratic in document count, a per-outcome file read.
"""

from __future__ import annotations

import time
from decimal import Decimal

import pytest

from docdoc.evaluation import (
    DocumentPrediction,
    Expectation,
    GoldenDocument,
    GoldenSet,
    Label,
    PredictionSet,
    Tier,
    evaluate,
    load_golden_set,
    load_prediction_set,
)
from docdoc.evaluation.ordering import path_key
from docdoc.evaluation.tiers import DocumentOrigin, OriginKind
from tests.fixtures.evaluation.datasets import INVOICE, MVP_ROOT, facts_for_fixtures, registry
from tests.support import make_extracted, make_extraction

pytestmark = pytest.mark.perf

#: Quality gate 5's target size: 50 documents, 500 labelled fields.
DOCUMENTS = 50
LABELS_PER_DOCUMENT = 10

SCORE_BUDGET_S = 0.050
END_TO_END_BUDGET_S = 0.500

_ORIGIN = DocumentOrigin(
    kind=OriginKind.SYNTHETIC,
    basis="Synthetic, generated for the performance bound.",
    generator_id="tests.perf.test_evaluation_perf",
    generator_version="1.0.0",
)


def _target_size_dataset() -> tuple[GoldenSet, PredictionSet]:
    """50 documents x 10 labels, half of them inside a repeating group.

    The entries matter: ``path_key`` decomposition and entry alignment are the two
    costs that scale with them, and a flat dataset would measure neither.
    """
    schema_hash = registry().resolve(INVOICE).schema_hash
    documents: list[GoldenDocument] = []
    labels: dict[str, tuple[Label, ...]] = {}
    predictions: dict[str, DocumentPrediction] = {}

    for index in range(DOCUMENTS):
        document_id = f"doc-{index:03d}"
        documents.append(
            GoldenDocument(
                document_id=document_id,
                blob_sha256="sha256:" + f"{index:064d}",
                tier=Tier.PUBLIC,
                origin=_ORIGIN,
                schema_identity=INVOICE,
                schema_hash=schema_hash,
                path=f"documents/{document_id}.pdf",
                declared_label_count=LABELS_PER_DOCUMENT,
            )
        )

        document_labels: list[Label] = [
            Label(field_path="invoice_number", expectation=Expectation.VALUE, value=f"INV-{index}"),
            Label(field_path="currency", expectation=Expectation.VALUE, value="USD"),
            Label(field_path="total", expectation=Expectation.VALUE, value=Decimal(f"{index}.00")),
            Label(field_path="supplier.tax_id", expectation=Expectation.ABSENT),
        ]
        entries = []
        for entry in range(3):
            document_labels.append(
                Label(
                    field_path=f"line_items[{entry}].description",
                    expectation=Expectation.VALUE,
                    value=f"item-{entry}",
                )
            )
            document_labels.append(
                Label(
                    field_path=f"line_items[{entry}].amount",
                    expectation=Expectation.VALUE,
                    value=Decimal(f"{entry}.50"),
                )
            )
            entries.append(
                {
                    "description": make_extracted(
                        f"line_items[{entry}].description", value=f"item-{entry}", present=True
                    ),
                    "amount": make_extracted(
                        f"line_items[{entry}].amount", value=Decimal(f"{entry}.50"), present=True
                    ),
                }
            )
        labels[document_id] = tuple(document_labels)

        values = {
            "invoice_number": make_extracted("invoice_number", value=f"INV-{index}", present=True),
            "currency": make_extracted("currency", value="USD", present=True),
            "total": make_extracted("total", value=Decimal(f"{index}.00"), present=True),
            "supplier": {"tax_id": make_extracted("supplier.tax_id", present=False)},
            "line_items": tuple(entries),
        }
        predictions[document_id] = DocumentPrediction(
            document_id=document_id,
            extraction=make_extraction(values, schema_identity=INVOICE, schema_hash=schema_hash),
            parser_id="perf",
            parser_version="1.0.0",
        )

    return (
        GoldenSet(documents=tuple(documents), labels=labels),
        PredictionSet(predictions=predictions, recorder_id="perf", recorder_version="1.0.0"),
    )


def _best(call, runs: int = 3) -> float:  # type: ignore[no-untyped-def]
    """Best of N. The minimum is the least noisy estimator of the real cost."""
    best = float("inf")
    for _ in range(runs):
        started = time.perf_counter()
        call()
        best = min(best, time.perf_counter() - started)
    return best


def test_the_dataset_is_the_size_the_bound_is_about() -> None:
    """A bound measured on the wrong size is not the bound anybody quoted."""
    golden, _predictions = _target_size_dataset()

    assert len(golden.documents) == DOCUMENTS
    assert sum(len(labels) for labels in golden.labels.values()) == DOCUMENTS * LABELS_PER_DOCUMENT


def test_scoring_a_target_size_dataset_is_under_the_bound() -> None:
    """< 50 ms for 500 labelled fields, excluding load (research.md R13)."""
    golden, predictions = _target_size_dataset()
    facts = facts_for_fixtures()

    elapsed = _best(lambda: evaluate(golden, predictions, facts=facts))

    assert elapsed < SCORE_BUDGET_S, (
        f"scoring {DOCUMENTS} documents took {elapsed * 1000:.1f} ms, over the "
        f"{SCORE_BUDGET_S * 1000:.0f} ms bound. The usual causes are a path key "
        "recomputed per comparison rather than cached, or an aggregation that went "
        "quadratic in document count"
    )


def test_the_committed_tier_loads_and_scores_end_to_end_under_the_bound() -> None:
    """< 500 ms including load, over the dataset a contributor actually runs.

    Smaller than the target size, so this is the weaker of the two bounds -- it
    exists to catch a per-outcome file read or a re-parse, which would show up
    here long before it showed up in the scoring number.
    """
    facts = facts_for_fixtures()

    def run() -> None:
        golden = load_golden_set(MVP_ROOT / "manifest.json", facts=facts)
        predictions = load_prediction_set(MVP_ROOT / "predictions", facts=facts)
        evaluate(golden, predictions, facts=facts)

    elapsed = _best(run)

    assert elapsed < END_TO_END_BUDGET_S, (
        f"load and score took {elapsed * 1000:.1f} ms, over the "
        f"{END_TO_END_BUDGET_S * 1000:.0f} ms bound. If load dominates, the fix is a "
        "leaner on-disk prediction form -- not a relaxed bound"
    )


def test_the_path_key_cache_is_doing_its_job() -> None:
    """The specific optimisation the bound assumes, measured directly.

    Decomposition costs ~1.85 us, which is ~0.93 ms per 500 fields if recomputed
    per comparison -- a fifth of the scoring budget spent parsing strings the
    caller already holds.
    """
    path_key.cache_clear()
    paths = [f"line_items[{index}].amount" for index in range(500)]

    cold = _best(lambda: [path_key(path) for path in paths], runs=1)
    warm = _best(lambda: [path_key(path) for path in paths])

    assert warm < cold, (
        f"cached lookups ({warm * 1000:.3f} ms) are no faster than cold ones "
        f"({cold * 1000:.3f} ms); the lru_cache is not being hit"
    )


def test_scoring_scales_linearly_in_document_count() -> None:
    """The shape check, which a single absolute bound cannot give.

    A quadratic aggregation passes a generous bound at 50 documents and falls over
    at 500 -- and 500 is the size this dataset is meant to grow to.
    """
    golden, predictions = _target_size_dataset()
    facts = facts_for_fixtures()

    half_ids = [d.document_id for d in golden.documents[: DOCUMENTS // 2]]
    half = golden.model_copy(
        update={
            "documents": golden.documents[: DOCUMENTS // 2],
            "labels": {k: v for k, v in golden.labels.items() if k in half_ids},
        }
    )
    half_predictions = predictions.model_copy(
        update={"predictions": {k: v for k, v in predictions.predictions.items() if k in half_ids}}
    )

    small = _best(lambda: evaluate(half, half_predictions, facts=facts))
    large = _best(lambda: evaluate(golden, predictions, facts=facts))

    # Generously bounded: linear would be 2x, and quadratic would be 4x. Three
    # leaves room for constant overhead and machine noise while still failing on
    # a genuine change of shape.
    assert large < small * 3, (
        f"doubling the dataset took {large / small:.1f}x as long, which is closer to "
        "quadratic than to linear"
    )
