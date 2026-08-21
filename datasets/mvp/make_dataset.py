"""Build the committed public tier of the golden set.

Run with ``uv run --extra pdf python datasets/mvp/make_dataset.py``. The outputs
are committed, so this exists for reproducibility and review rather than to run
in CI -- the same arrangement, and the same reasoning, as
``tests/fixtures/make_fixtures.py``.

**Scoring the committed dataset needs none of this.** ``uv sync --extra dev`` is
enough to run ``examples/evaluate_golden_set.py`` and every test, because the
predictions are committed and replayed. The ``--extra pdf`` above is needed only
to *regenerate*, because regeneration parses the documents, and PyMuPDF is the
parser. That asymmetry is the point of committing predictions at all: the
contributor's path has no dependencies, and the maintainer's path is the one that
carries them.

**Every document is synthetic**, written here, with its generator and version
recorded in its ``origin``. No real document content and nothing that could carry
PII ever enters the repository (Constitution §Security), and a synthetic document
whose generator is unknown cannot be regenerated -- which would make its labels
unverifiable (FR-011, EVA-2b).

**Labels are authored, never derived from the predictions.** A dataset whose
truth was read off the model's answers would score 100% by construction and
measure nothing. The two are written independently below, and they disagree in
places on purpose.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
REPO = HERE.parents[1]
DOCUMENTS = HERE / "documents"
LABELS = HERE / "labels"
PREDICTIONS = HERE / "predictions"

GENERATOR_ID = "datasets.mvp.make_dataset"
GENERATOR_VERSION = "1.0.0"

WIDTH, HEIGHT = 595.0, 842.0

INVOICE = "invoice@1"
RECEIPT = "receipt@1"


# ---------------------------------------------------------------------------
# The documents, as printed text
# ---------------------------------------------------------------------------

PAGES: dict[str, list[str]] = {
    "invoice-001": [
        "ACME SUPPLIES LIMITED",
        "Invoice INV-001",
        "Issued 2026-03-01",
        "Currency USD",
        "",
        "Description            Qty    Unit      Amount",
        "Widget, large            2   500.00    1000.00",
        "Delivery                 1   240.00     240.00",
        "",
        "Total                                  1240.00",
    ],
    "invoice-002": [
        "NORTHWIND TRADING COMPANY",
        "Invoice INV-002",
        "Issued 2026-03-08",
        "Currency EUR",
        "Tax registration DE123456789",
        "",
        "Description            Qty    Unit      Amount",
        "Consulting, March        8   125.00    1000.00",
        "",
        "Total                                  1000.00",
    ],
    # Deliberately more than 100 meaningful characters per page, so `text-layer@1`
    # judges these text-bearing by a comfortable margin rather than by a hair.
    # A receipt is short in real life, and the two below would otherwise route to
    # a recognition-backed parser that this repository's offline path does not
    # have -- the same margin `tests/fixtures/make_fixtures.py` keeps, for the
    # same reason.
    "receipt-001": [
        "CORNER STORE",
        "18 Harbour Road, Port Meadow",
        "VAT registration GB 123 4567 89",
        "2026-03-02 14:05",
        "",
        "Sundries                                12.50",
        "Total                                   12.50",
        "Paid by card",
        "Thank you for your custom. Goods may be",
        "returned within 28 days with this receipt.",
    ],
    "receipt-002": [
        "HARBOUR CAFE",
        "4 Quayside Lane, Port Meadow",
        "VAT registration GB 987 6543 21",
        "2026-03-09 09:20",
        "",
        "Coffee                                   3.40",
        "Total                                    3.40",
        "Paid by cash",
        "Thank you for your custom. Please retain",
        "this receipt as proof of purchase.",
    ],
}

SCHEMAS = {
    "invoice-001": INVOICE,
    "invoice-002": INVOICE,
    "receipt-001": RECEIPT,
    "receipt-002": RECEIPT,
}


# ---------------------------------------------------------------------------
# The truth, authored by hand
# ---------------------------------------------------------------------------


def _value(field_path: str, value: Any, **extra: Any) -> dict[str, Any]:
    return {
        "field_path": field_path,
        "expectation": "value",
        "value": value,
        "labeler": "docdoc-maintainers",
        "labeled_at": "2026-08-20T00:00:00",
        **extra,
    }


def _absent(field_path: str) -> dict[str, Any]:
    return {
        "field_path": field_path,
        "expectation": "absent",
        "labeler": "docdoc-maintainers",
        "labeled_at": "2026-08-20T00:00:00",
    }


LABEL_SETS: dict[str, list[dict[str, Any]]] = {
    "invoice-001": [
        # An expected location, so the mislocation rate has something to measure.
        # A page and a generous box, because a hand-drawn label is loose and
        # `page_box@1` asks for containment rather than IoU (EVA-14a).
        _value("invoice_number", "INV-001", location={"page": 0, "bbox": [0.0, 0.0, 1.0, 0.5]}),
        _value("issue_date", "2026-03-01"),
        _value("currency", "USD"),
        _value("total", "1240.00"),
        _value("supplier.legal_name", "ACME SUPPLIES LIMITED"),
        _absent("supplier.tax_id"),
        _absent("due_date"),
        _value("line_items[0].description", "Widget, large"),
        _value("line_items[0].amount", "1000.00"),
        _value("line_items[1].description", "Delivery"),
        _value("line_items[1].amount", "240.00"),
    ],
    "invoice-002": [
        _value("invoice_number", "INV-002"),
        _value("issue_date", "2026-03-08"),
        _value("currency", "EUR"),
        _value("total", "1000.00"),
        _value("supplier.legal_name", "NORTHWIND TRADING COMPANY"),
        # Printed on the document, and the model below misses it. A real MISSING.
        _value("supplier.tax_id", "DE123456789"),
        _absent("due_date"),
        _value("line_items[0].description", "Consulting, March"),
        _value("line_items[0].amount", "1000.00"),
    ],
    "receipt-001": [
        _value("merchant_name", "CORNER STORE"),
        _value("purchased_at", "2026-03-02T14:05:00"),
        _value("total", "12.50"),
        _value("payment_method", "card"),
    ],
    "receipt-002": [
        _value("merchant_name", "HARBOUR CAFE"),
        _value("purchased_at", "2026-03-09T09:20:00"),
        _value("total", "3.40"),
        # The model below answers "cash", which the schema allows and the truth
        # agrees with -- so this one is correct, and the dataset is not uniformly
        # pessimistic either.
        _value("payment_method", "cash"),
    ],
}


# ---------------------------------------------------------------------------
# What the model answers, authored separately and deliberately imperfect
# ---------------------------------------------------------------------------


def _echo(value: Any, claimed: str | None = None) -> dict[str, Any]:
    return {"value": value, "claimed_text": claimed, "confidence": None}


_NONE = {"value": None, "claimed_text": None, "confidence": None}

RESPONSES: dict[str, dict[str, Any]] = {
    "invoice-001": {
        "invoice_number": _echo("INV-001", "INV-001"),
        "issue_date": _echo("2026-03-01", "2026-03-01"),
        "due_date": _NONE,
        "currency": _echo("USD", "USD"),
        "total": _echo("1240.00", "1240.00"),
        "supplier": {
            "legal_name": _echo("ACME SUPPLIES LIMITED", "ACME SUPPLIES LIMITED"),
            "tax_id": _NONE,
        },
        "line_items": [
            {
                "description": _echo("Widget, large", "Widget, large"),
                "quantity": _echo(2.0, "2"),
                "unit_price": _echo("500.00", "500.00"),
                "amount": _echo("1000.00", "1000.00"),
            },
            {
                "description": _echo("Delivery", "Delivery"),
                "quantity": _echo(1.0, "1"),
                "unit_price": _echo("240.00", "240.00"),
                "amount": _echo("240.00", "240.00"),
            },
        ],
    },
    "invoice-002": {
        "invoice_number": _echo("INV-002", "INV-002"),
        "issue_date": _echo("2026-03-08", "2026-03-08"),
        "due_date": _NONE,
        "currency": _echo("EUR", "EUR"),
        "total": _echo("1000.00", "1000.00"),
        "supplier": {
            "legal_name": _echo("NORTHWIND TRADING COMPANY", "NORTHWIND TRADING COMPANY"),
            # Printed, and not read. MISSING rather than INCORRECT.
            "tax_id": _NONE,
        },
        "line_items": [
            {
                "description": _echo("Consulting, March", "Consulting, March"),
                "quantity": _echo(8.0, "8"),
                "unit_price": _echo("125.00", "125.00"),
                "amount": _echo("1000.00", "1000.00"),
            }
        ],
    },
    "receipt-001": {
        "merchant_name": _echo("CORNER STORE", "CORNER STORE"),
        "purchased_at": _echo("2026-03-02T14:05:00", "2026-03-02 14:05"),
        # Misread: a transposition, which is the failure mode a decimal comparator
        # exists to catch. INCORRECT, not MISSING.
        "total": _echo("12.05", "12.50"),
        "payment_method": _echo("card", "card"),
    },
    "receipt-002": {
        "merchant_name": _echo("HARBOUR CAFE", "HARBOUR CAFE"),
        "purchased_at": _echo("2026-03-09T09:20:00", "2026-03-09 09:20"),
        "total": _echo("3.40", "3.40"),
        "payment_method": _echo("cash", "cash"),
    },
}

#: The restricted tier: declared, referenced by hash, never committed. Its
#: ``declared_label_count`` is what lets a run without the bundle state its
#: covered fraction as exact integers rather than estimating it (EVA-5a).
RESTRICTED = [
    {
        "document_id": "restricted-invoice-001",
        "blob_sha256": "sha256:" + "1" * 64,
        "declared_label_count": 11,
    },
    {
        "document_id": "restricted-invoice-002",
        "blob_sha256": "sha256:" + "2" * 64,
        "declared_label_count": 9,
    },
]


def _write_pdf(name: str, lines: list[str]) -> bytes:
    import pymupdf

    document = pymupdf.open()
    page = document.new_page(width=WIDTH, height=HEIGHT)
    y = 80.0
    for line in lines:
        if line:
            page.insert_text((60.0, y), line, fontsize=11)
        y += 16.0
    data: bytes = document.tobytes()
    document.close()

    path = DOCUMENTS / f"{name}.pdf"
    path.write_bytes(data)
    return data


def main() -> int:
    from docdoc.evaluation import schema_facts
    from docdoc.extraction import SchemaRegistry
    from docdoc.extraction.adapters import EchoAdapter
    from docdoc.ingest import parse
    from docdoc.recording import record_predictions, write_prediction_set

    for directory in (DOCUMENTS, LABELS, PREDICTIONS):
        directory.mkdir(parents=True, exist_ok=True)

    registry = SchemaRegistry.from_paths([REPO / "schemas"])

    documents: dict[str, Any] = {}
    entries: list[dict[str, Any]] = []

    for name, lines in PAGES.items():
        data = _write_pdf(name, lines)
        parsed = parse(data)
        documents[name] = parsed
        identity = SCHEMAS[name]
        entries.append(
            {
                "document_id": name,
                "blob_sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
                "tier": "public",
                "origin": {
                    "kind": "synthetic",
                    "basis": (
                        "Synthetic. Written for this repository's golden set; no real "
                        "document content and nothing that could carry PII."
                    ),
                    "source": "datasets/mvp/make_dataset.py",
                    "generator_id": GENERATOR_ID,
                    "generator_version": GENERATOR_VERSION,
                },
                "schema_identity": identity,
                "schema_hash": registry.resolve(identity).schema_hash,
                "path": f"documents/{name}.pdf",
                "declared_label_count": len(LABEL_SETS[name]),
            }
        )
        (LABELS / f"{name}.json").write_text(
            json.dumps(LABEL_SETS[name], indent=2) + "\n", encoding="utf-8"
        )

    for restricted in RESTRICTED:
        entries.append(
            {
                **restricted,
                "tier": "restricted",
                "origin": {
                    "kind": "restricted",
                    "basis": (
                        "Held by the corpus owner under terms that forbid "
                        "redistribution. Referenced by content hash only."
                    ),
                },
                "schema_identity": INVOICE,
                "schema_hash": registry.resolve(INVOICE).schema_hash,
            }
        )

    public = [entry for entry in entries if entry["tier"] == "public"]
    manifest = {
        "name": "docdoc-mvp",
        "generator": {"id": GENERATOR_ID, "version": GENERATOR_VERSION},
        # Stated per tier, never merged (FR-009). Quality gate 5 counts the
        # public tier alone -- CI cannot see the other one -- so a merged total
        # would make the gate unreadable.
        "size": {
            "public": {
                "documents": len(public),
                "labelled_fields": sum(int(e["declared_label_count"]) for e in public),
            },
            "restricted": {
                "documents": len(RESTRICTED),
                "labelled_fields": sum(int(e["declared_label_count"]) for e in RESTRICTED),
            },
            # Recorded here so the distance to the constitution's fifth quality
            # gate is a number a reader can see rather than a gap nobody
            # mentions. The gate stays advisory (ADR-0009, constitution v1.4.0).
            "gate_5_target": {"documents": 50, "labelled_fields": 500},
        },
        "documents": entries,
    }
    (HERE / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    golden_facts = schema_facts([registry.describe(i) for i in registry.identities()])
    from docdoc.evaluation import load_golden_set

    golden = load_golden_set(HERE / "manifest.json", facts=golden_facts)

    adapters = {name: EchoAdapter.returning(SCHEMAS[name], RESPONSES[name]) for name in PAGES}

    class _PerDocumentAdapter:
        """One canned answer per document, behind one adapter identity.

        The echo adapter keys its responses by schema identity, and this dataset
        has four documents across two schemas. Dispatching by the requested
        schema alone would give both invoices the same answer, which would make
        the dataset measure one document twice.
        """

        def __init__(self) -> None:
            self._current = next(iter(adapters.values()))

        def use(self, name: str) -> None:
            self._current = adapters[name]

        def __getattr__(self, item: str) -> Any:
            return getattr(self._current, item)

    dispatcher = _PerDocumentAdapter()

    recorded = {}
    for name in PAGES:
        dispatcher.use(name)
        one = golden.model_copy(
            update={
                "documents": tuple(d for d in golden.documents if d.document_id == name),
                "labels": {name: golden.labels_for(name)},
            }
        )
        result = record_predictions(
            one, adapter=dispatcher, registry=registry, documents={name: documents[name]}
        )
        recorded.update(result.predictions)

    from docdoc.evaluation import PredictionSet
    from docdoc.recording import RECORDER_ID, RECORDER_VERSION

    write_prediction_set(
        PredictionSet(
            predictions=recorded,
            recorder_id=RECORDER_ID,
            recorder_version=RECORDER_VERSION,
        ),
        PREDICTIONS,
    )

    print(f"wrote {len(public)} public documents and {len(recorded)} predictions to {HERE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
