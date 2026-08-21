"""Score the same document twice with one deliberate regression, and read the delta.

Runs standalone with no credentials, no network, no database, and no object
storage:

    uv run python examples/compare_reports.py

No ``--extra pdf`` either: the document is built here from the kernel's own
primitives, the way ``build_document.py`` shows. That keeps this example on the
same footing as everything else a contributor runs.

The story: a model upgrade lands, and the invoice total comes back with two
digits transposed. One field breaks. Accuracy falls a couple of points. Somebody
notices next month.

What ``compare()`` gives instead is the field that broke, by name and in both
directions; the metrics that moved, each with what it divided; the **named**
grounding regression, because the constitution's fourth quality gate blocks on
that one and a gate cannot search a table for the row that matters; and
``provenance_differences``, which is what turns "accuracy fell and the model
changed" from a coincidence into a finding.

It states all of that and **decides nothing** (FR-049). Whether a build fails is
policy configured on top of this output; a comparison that also decided would
bury the decision inside the thing being measured.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from typing import Any

from docdoc.evaluation import (
    DocumentOrigin,
    Expectation,
    GoldenDocument,
    GoldenSet,
    Judgement,
    Label,
    OriginKind,
    Tier,
    compare,
    evaluate,
    schema_facts,
)
from docdoc.extraction import SchemaRegistry
from docdoc.extraction.adapters import EchoAdapter
from docdoc.kernel import (
    BBox,
    BlobRef,
    Capabilities,
    Document,
    Geometry,
    IngestProvenance,
    Page,
    Span,
    Token,
    blob_id_for,
    options_hash_for,
)
from docdoc.recording import record_predictions

REPO = Path(__file__).resolve().parent.parent
SCHEMA = "invoice@1"

TEXT = (
    "ACME SUPPLIES LIMITED\n"
    "Invoice INV-001\n"
    "Issued 2026-03-01\n"
    "Currency USD\n"
    "Widget, large 2 500.00 1000.00\n"
    "Delivery 1 240.00 240.00\n"
    "Total 1240.00\n"
)

RAW = b"(pretend this is the source file)"


def build_document() -> Document:
    """One page, one token per word, with geometry laid out left to right.

    A parser would produce this. Doing it by hand keeps the example free of the
    ``pdf`` extra, and the exact boxes do not matter — only that they are stable
    and that each token maps to the page whose span contains it.
    """
    page = Page(index=0, span=Span(0, len(TEXT)), width=612.0, height=792.0, rotation=0)

    tokens: list[Token] = []
    position = 0
    words = 0
    while position < len(TEXT):
        if TEXT[position].isspace():
            position += 1
            continue
        end = position
        while end < len(TEXT) and not TEXT[end].isspace():
            end += 1
        slot = words * 0.02
        tokens.append(
            Token(
                span=Span(position, end),
                geometry=Geometry(
                    page_index=0,
                    bbox=BBox(round(0.05 + slot, 4), 0.1, round(0.07 + slot, 4), 0.13),
                ),
                source_confidence=0.99,
            )
        )
        words += 1
        position = end

    options: dict[str, object] = {}
    return Document.create(
        text=TEXT,
        pages=(page,),
        tokens=tuple(tokens),
        provenance=IngestProvenance(
            parser_id="example-text-reader",
            parser_version="1.0.0",
            options=options,
            options_hash=options_hash_for(options),
            capabilities=Capabilities(text=True, geometry=True, tables=False, handwriting=False),
            text_layer_used=True,
        ),
        source=BlobRef(
            blob_id=blob_id_for(RAW),
            mime_type="application/pdf",
            size_bytes=len(RAW),
            filename="invoice-001.pdf",
        ),
    )


def _echo(value: Any, claimed: str | None = None) -> dict[str, Any]:
    return {"value": value, "claimed_text": claimed, "confidence": None}


_ABSENT = {"value": None, "claimed_text": None, "confidence": None}


def responses(total: str, claimed: str) -> dict[str, Any]:
    """What the model answers. Only the total differs between the two runs."""
    return {
        "invoice_number": _echo("INV-001", "INV-001"),
        "issue_date": _echo("2026-03-01", "2026-03-01"),
        "due_date": _ABSENT,
        "currency": _echo("USD", "USD"),
        "total": _echo(total, claimed),
        "supplier": {
            "legal_name": _echo("ACME SUPPLIES LIMITED", "ACME SUPPLIES LIMITED"),
            "tax_id": _ABSENT,
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
    }


def golden_set(schema_hash: str) -> GoldenSet:
    """The truth, authored by hand — never read off the model's answers."""
    labels = (
        Label(field_path="invoice_number", expectation=Expectation.VALUE, value="INV-001"),
        Label(field_path="issue_date", expectation=Expectation.VALUE, value=date(2026, 3, 1)),
        Label(field_path="currency", expectation=Expectation.VALUE, value="USD"),
        Label(field_path="total", expectation=Expectation.VALUE, value=Decimal("1240.00")),
        Label(
            field_path="supplier.legal_name",
            expectation=Expectation.VALUE,
            value="ACME SUPPLIES LIMITED",
        ),
        Label(field_path="supplier.tax_id", expectation=Expectation.ABSENT),
        Label(field_path="due_date", expectation=Expectation.ABSENT),
    )
    return GoldenSet(
        documents=(
            GoldenDocument(
                document_id="invoice-001",
                blob_sha256=blob_id_for(RAW),
                tier=Tier.PUBLIC,
                origin=DocumentOrigin(
                    kind=OriginKind.SYNTHETIC,
                    basis="Synthetic, written for this example.",
                    generator_id="examples.compare_reports",
                    generator_version="1.0.0",
                ),
                schema_identity=SCHEMA,
                schema_hash=schema_hash,
                path="examples/compare_reports.py",
                declared_label_count=len(labels),
            ),
        ),
        labels={"invoice-001": labels},
    )


def main() -> int:
    registry = SchemaRegistry.from_paths([REPO / "schemas"])
    facts = schema_facts([registry.describe(identity) for identity in registry.identities()])
    golden = golden_set(registry.resolve(SCHEMA).schema_hash)
    documents = {"invoice-001": build_document()}

    def score(adapter: EchoAdapter) -> Any:
        return evaluate(
            golden,
            record_predictions(golden, adapter=adapter, registry=registry, documents=documents),
            facts=facts,
        )

    before = score(EchoAdapter.returning(SCHEMA, responses("1240.00", "1240.00")))

    # The regression. The total is misread, and the text it claims to have read
    # it from is no longer in the document -- so the value is both wrong and
    # ungrounded, which is how a regression usually arrives.
    #
    # Recorded under a **new model version**, because a different answer comes
    # from something identified having changed. Under the old version the
    # artifact chain would not move: ADR-0003 identifies an artifact by its
    # inputs, so both runs would claim to be the same measurement and comparing
    # them would compare a report against itself.
    after = score(
        EchoAdapter(
            {SCHEMA: responses("1204.00", "1,204.00")},
            version="1.1.0",
            model_version="2",
        )
    )

    delta = compare(before, after)

    print("Metrics that moved")
    for name, movement in sorted(delta.metrics.items()):
        if movement.judgement is Judgement.UNCHANGED:
            continue
        change = "n/a" if movement.delta is None else f"{movement.delta:+.4f}"
        print(f"  {name:<18} {movement.judgement:<12} {change:>9}")

    print("\nFields that broke")
    for outcome in delta.broke:
        print(f"  {outcome.document_id}/{outcome.field_path}: {outcome.before} -> {outcome.after}")
    print("Fields that were fixed")
    for outcome in delta.fixed:
        print(f"  {outcome.document_id}/{outcome.field_path}: {outcome.before} -> {outcome.after}")
    if not delta.fixed:
        print("  (none)")

    print("\nGrounding")
    if delta.grounding_regression is None:
        print("  no regression")
    else:
        movement = delta.grounding_regression
        assert movement.before is not None
        assert movement.after is not None
        print(f"  REGRESSION  {movement.before.value:.4f} -> {movement.after.value:.4f}")
        print("  named on its own field, because a quality gate cannot read a table")

    print("\nWhat differed between the two runs")
    for name in delta.provenance_differences:
        print(f"  {name}")
    if not delta.provenance_differences:
        print("  (nothing - so the movement above is unattributed)")

    print("\nThis output states what moved. It decides nothing about it (FR-049).")
    print(f"  before {delta.before_report_id}")
    print(f"  after  {delta.after_report_id}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
