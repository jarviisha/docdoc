"""T005 — a small, well-formed golden set, and everything a scorer must handle.

Small enough to compute every metric by hand (which `test_metric_definitions.py`
does, from literals), and wide enough that no code path in the scorer goes
unexercised. What it deliberately contains:

- **Two schemas**, ``invoice@1`` and ``receipt@1``. FR-013 says a dataset is not
  invoice-shaped, and a fixture with one schema would let a document-type
  assumption survive every test in the suite.
- **Value labels and absence labels.** They have different denominators (EVA-17),
  so a fixture carrying only the first would leave half the arithmetic untested.
- **A repeating group aligned positionally**, and the same group **aligned by a
  declared key** in :func:`keyed_golden_set`. Two builders rather than one set,
  because ``EntryKeySpec`` is declared per group path for the whole dataset --
  a single set cannot align one group both ways, and pretending otherwise would
  have meant inventing a per-document key syntax that the format does not have.
- **Labels with an expected location and labels with none.** FR-018 makes the
  location optional, and the second kind is what proves the mislocation
  denominator excludes them rather than counting them as agreeing.
- **One restricted-tier document**, whose labels are absent but whose
  ``declared_label_count`` is not. That single number is what lets a partial
  report state its covered fraction exactly (EVA-5a).

The schemas are the repository's real ones, loaded from ``schemas/``. Labels
written against an invented fixture schema would pass while telling nobody
whether a real one works.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from docdoc.evaluation import (
    EntryKeySpec,
    Expectation,
    ExpectedLocation,
    GoldenDocument,
    GoldenSet,
    Label,
    Tier,
    schema_facts,
)
from docdoc.evaluation.tiers import DocumentOrigin, OriginKind
from docdoc.extraction import SchemaRegistry
from docdoc.kernel import BBox

if TYPE_CHECKING:
    from docdoc.evaluation import SchemaFacts

__all__ = [
    "DOCUMENT_TEXT",
    "INVOICE",
    "MVP_ROOT",
    "RECEIPT",
    "committed_golden_set",
    "committed_prediction_set",
    "facts_for_fixtures",
    "golden_set",
    "keyed_golden_set",
    "registry",
]

INVOICE = "invoice@1"
RECEIPT = "receipt@1"

#: The generator every synthetic document in this fixture names. A synthetic
#: document whose generator is unknown cannot be regenerated, which makes its
#: labels unverifiable -- so ``DocumentOrigin`` refuses one, and the fixtures
#: have to say who made them like any other document (FR-011, EVA-2b).
GENERATOR = ("tests.fixtures.evaluation.datasets", "1.0.0")

#: The source text of each fixture document. Held here rather than in a binary so
#: a reviewer can see what is being labelled, and so `predictions.py` can build a
#: real ``Document`` from it without a parser or a PDF dependency.
DOCUMENT_TEXT = {
    "clean": (
        "ACME LTD\nInvoice INV-001\nIssued 2026-03-01\nCurrency USD\n"
        "Widget, large  2  500.00  1000.00\nDelivery  1  240.00  240.00\nTotal 1240.00\n"
    ),
    "near-miss": (
        "ACME LTD\nInvoice INV-002\nIssued 2026-03-02\nCurrency USD\n"
        "Widget, small  3  100.00  300.00\nTotal 300.00\n"
    ),
    "keyed": (
        "ACME LTD\nInvoice INV-003\nIssued 2026-03-03\nCurrency EUR\n"
        "SKU-B  1  50.00  50.00\nSKU-A  2  25.00  50.00\nTotal 100.00\n"
    ),
    "receipt": ("CORNER STORE\n2026-03-02 14:05\nTotal 12.50\nPaid by card\n"),
    "failing": ("ACME LTD\nInvoice INV-004\nIssued 2026-03-04\nCurrency GBP\nTotal 999.00\n"),
    "silent": ("ACME LTD\nInvoice INV-005\nIssued 2026-03-05\nCurrency USD\nTotal 5.00\n"),
}


def registry() -> SchemaRegistry:
    """The repository's real schemas, not an invented pair."""
    return SchemaRegistry.from_paths(["schemas"])


def facts_for_fixtures() -> SchemaFacts:
    """Declared paths, scalars, and types for both fixture schemas."""
    reg = registry()
    return schema_facts([reg.describe(identity) for identity in reg.identities()])


def _origin(kind: OriginKind = OriginKind.SYNTHETIC) -> DocumentOrigin:
    generator_id, generator_version = GENERATOR
    return DocumentOrigin(
        kind=kind,
        basis=(
            "Synthetic. Written for this test suite; no real document content and "
            "nothing that could carry PII enters the repository."
        ),
        source="tests/fixtures/evaluation/datasets.py",
        generator_id=generator_id,
        generator_version=generator_version,
    )


def _document(
    document_id: str,
    *,
    schema_identity: str,
    declared_label_count: int,
    tier: Tier = Tier.PUBLIC,
) -> GoldenDocument:
    reg = registry()
    return GoldenDocument(
        document_id=document_id,
        blob_sha256="sha256:" + document_id.replace("-", "")[:8].ljust(64, "0"),
        tier=tier,
        origin=_origin(OriginKind.SYNTHETIC if tier is Tier.PUBLIC else OriginKind.RESTRICTED),
        schema_identity=schema_identity,
        schema_hash=reg.resolve(schema_identity).schema_hash,
        path=None if tier is Tier.RESTRICTED else f"datasets/fixtures/{document_id}.txt",
        declared_label_count=declared_label_count,
    )


def _value(
    field_path: str,
    value: object,
    *,
    location: ExpectedLocation | None = None,
) -> Label:
    return Label(
        field_path=field_path,
        expectation=Expectation.VALUE,
        value=value,
        location=location,
        labeler="fixtures",
        labeled_at=datetime(2026, 8, 19, 12, 0, 0),
    )


def _absent(field_path: str) -> Label:
    return Label(
        field_path=field_path,
        expectation=Expectation.ABSENT,
        labeler="fixtures",
        labeled_at=datetime(2026, 8, 19, 12, 0, 0),
    )


# -- the labels, per document ------------------------------------------------
#
# Written out rather than generated. A fixture whose expected values are computed
# is a fixture that agrees with whatever the code does.

CLEAN_LABELS = (
    # A location with a box, and one without: FR-018 makes the location optional,
    # and the two kinds take different paths through `page_box@1`.
    _value(
        "invoice_number",
        "INV-001",
        location=ExpectedLocation(page=0, bbox=BBox(0.0, 0.0, 1.0, 1.0)),
    ),
    _value("issue_date", date(2026, 3, 1)),
    _value("currency", "USD"),
    _value("total", Decimal("1240.00")),
    _value("supplier.legal_name", "ACME LTD"),
    # An absence label. The A partition of EVA-17, with its own denominator.
    _absent("supplier.tax_id"),
    _absent("due_date"),
    _value("line_items[0].description", "Widget, large"),
    _value("line_items[0].amount", Decimal("1000.00")),
    _value("line_items[1].description", "Delivery"),
    _value("line_items[1].amount", Decimal("240.00")),
)

NEAR_MISS_LABELS = (
    _value("invoice_number", "INV-002"),
    _value("issue_date", date(2026, 3, 2)),
    _value("currency", "USD"),
    # The prediction says 300.00; the truth says 350.00. INCORRECT, not MISSING --
    # a wrong answer and a blank are different failures with different fixes.
    _value("total", Decimal("350.00")),
    # The prediction reports this absent. MISSING.
    _value("supplier.legal_name", "ACME LTD"),
    # The prediction invents one. SPURIOUS.
    _absent("supplier.tax_id"),
)

KEYED_LABELS = (
    _value("invoice_number", "INV-003"),
    _value("issue_date", date(2026, 3, 3)),
    _value("currency", "EUR"),
    _value("total", Decimal("100.00")),
    _value("supplier.legal_name", "ACME LTD"),
    # Entries are labelled in a different order than the prediction records them.
    # Positionally these all mismatch; keyed by `description` they all agree,
    # which is the whole point of the two builders below.
    _value("line_items[0].description", "SKU-A"),
    _value("line_items[0].amount", Decimal("50.00")),
    _value("line_items[1].description", "SKU-B"),
    _value("line_items[1].amount", Decimal("50.00")),
)

RECEIPT_LABELS = (
    _value("merchant_name", "CORNER STORE"),
    _value("purchased_at", datetime(2026, 3, 2, 14, 5)),
    _value("total", Decimal("12.50")),
    _value("payment_method", "card"),
)

#: The document that fails to process. Its labels stay in every denominator and
#: count as MISSING, which is what stops a crash reading as an improvement.
FAILING_LABELS = (
    _value("invoice_number", "INV-004"),
    _value("issue_date", date(2026, 3, 4)),
    _value("currency", "GBP"),
    _value("total", Decimal("999.00")),
)

#: The document with no prediction at all. UNEVALUATED, and a different fact from
#: a failure -- both stay in the denominator, only one is a defect.
SILENT_LABELS = (
    _value("invoice_number", "INV-005"),
    _value("total", Decimal("5.00")),
)

DOCUMENTS = (
    ("clean", INVOICE, CLEAN_LABELS),
    ("near-miss", INVOICE, NEAR_MISS_LABELS),
    ("keyed", INVOICE, KEYED_LABELS),
    ("receipt", RECEIPT, RECEIPT_LABELS),
    ("failing", INVOICE, FAILING_LABELS),
    ("silent", INVOICE, SILENT_LABELS),
)

#: How many labels the restricted document declares. Committed even though its
#: labels are not, which is what makes a partial report's covered fraction exact
#: rather than estimated (EVA-5a).
RESTRICTED_LABEL_COUNT = 4


def golden_set(*, include_restricted: bool = True) -> GoldenSet:
    """The well-formed set, with ``line_items`` aligned positionally."""
    documents = [
        _document(name, schema_identity=schema, declared_label_count=len(labels))
        for name, schema, labels in DOCUMENTS
    ]
    labels = {name: labels for name, _schema, labels in DOCUMENTS}

    if include_restricted:
        documents.append(
            _document(
                "restricted-invoice",
                schema_identity=INVOICE,
                declared_label_count=RESTRICTED_LABEL_COUNT,
                tier=Tier.RESTRICTED,
            )
        )

    return GoldenSet(
        documents=tuple(sorted(documents, key=lambda d: d.document_id)),
        labels=labels,
    )


def keyed_golden_set() -> GoldenSet:
    """The same set with ``line_items`` aligned by its ``description`` field.

    The key is declared **here, by the dataset** -- never by the schema. A key in
    the schema would move ``schema_hash`` under ADR-0008, and FR-004 would then
    refuse every label already written against it: declaring a key to fix
    alignment would invalidate the dataset it was meant to measure (FR-020).
    """
    base = golden_set()
    return base.model_copy(
        update={"entry_keys": (EntryKeySpec(group_path="line_items", key_field="description"),)}
    )


# ---------------------------------------------------------------------------
# The committed public tier, under datasets/mvp/
# ---------------------------------------------------------------------------
#
# Distinct from the fixtures above and not a replacement for them. The fixtures
# are shaped to exercise every code path -- a failed document, a document with no
# prediction, a keyed group -- which makes them a poor demonstration of what a
# real dataset looks like. The committed tier is the opposite: it is what a
# contributor actually scores, and its numbers are the ones the README quotes.

MVP_ROOT = Path(__file__).resolve().parents[3] / "datasets" / "mvp"


def committed_golden_set(*, restricted_bundle: str | Path | None = None) -> GoldenSet:
    """The committed public tier, loaded from disk exactly as a contributor does."""
    from docdoc.evaluation import load_golden_set

    return load_golden_set(
        MVP_ROOT / "manifest.json",
        restricted_bundle=restricted_bundle,
        facts=facts_for_fixtures(),
    )


def committed_prediction_set() -> Any:
    """The committed predictions, replayed rather than re-recorded."""
    from docdoc.evaluation import load_prediction_set

    return load_prediction_set(MVP_ROOT / "predictions", facts=facts_for_fixtures())
