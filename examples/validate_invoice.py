"""Reject an invoice whose total does not add up -- and point at the page.

    uv run python examples/validate_invoice.py

No credentials, no network, no database, and -- unlike every earlier stage -- no
document is passed to the validator at all. Every location printed below was
computed by the grounding stage and copied through, so this example cannot show a
place grounding would not have shown.

The second invoice is the case the whole milestone is about. Every field in it is
individually well formed: the number matches its pattern, the currency is in its
enum, every amount parses as a decimal, and the model was confident. The
arithmetic is wrong, and no amount of per-field checking would notice.
"""

from __future__ import annotations

from decimal import Decimal

from docdoc.extraction.adapter import ExtractionOptions, ModelUsage
from docdoc.extraction.extract import ExtractionProvenance, ExtractionResult
from docdoc.extraction.identity import schema_hash_for
from docdoc.extraction.schema import (
    Cardinality,
    FieldSpec,
    FieldType,
    RuleKind,
    RuleSpec,
    Schema,
)
from docdoc.extraction.value import ExtractedValue
from docdoc.grounding import ground
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
from docdoc.validation import Severity, Verdict, validate

DOCUMENT_TEXT = """\
ACME SUPPLIES LIMITED

Invoice INV-2026-001
Issued 2026-01-15

Widget A    2 x 500.00    1000.00
Widget B    1 x 420.00     420.00

Total EUR 1420.00
"""

# The rule Principle VII names by example, declared as *data*. It is not a prompt
# instruction and not a Python callback: one generic engine evaluates it, and that
# same engine evaluates every other schema's rules without knowing what an invoice
# is. Adding a document type stays a matter of adding files, never code.
TOTAL_MATCHES_LINES = RuleSpec(
    id="total_matches_lines",
    kind=RuleKind.SUM_EQUALS,
    operands=("line_items.amount", "total"),
    tolerance=Decimal("0.00"),  # declared, never inferred
)

SCHEMA = Schema(
    name="example_invoice",
    version=1,
    rules=(TOTAL_MATCHES_LINES,),
    fields=(
        FieldSpec(
            name="number",
            type=FieldType.STRING,
            required=True,
            constraints={"pattern": r"INV-\d{4}-\d{3}"},
        ),
        FieldSpec(
            name="currency",
            type=FieldType.STRING,
            required=True,
            constraints={"enum": ["EUR", "USD", "VND"]},
        ),
        FieldSpec(name="total", type=FieldType.DECIMAL, required=True),
        FieldSpec(
            name="line_items",
            cardinality=Cardinality.REPEATING_GROUP,
            fields=(
                FieldSpec(name="description", type=FieldType.STRING, required=True),
                FieldSpec(name="amount", type=FieldType.DECIMAL, required=True),
            ),
        ),
    ),
)


def build_document() -> Document:
    """One page, one token per word, with a box for each so a finding can carry one."""
    pages = (Page(index=0, span=Span(0, len(DOCUMENT_TEXT)), width=612.0, height=792.0),)

    tokens: list[Token] = []
    cursor = 0
    for row, line in enumerate(DOCUMENT_TEXT.splitlines(keepends=True)):
        column = 0
        for word in line.split():
            offset = line.index(word, column)
            column = offset + len(word)
            start = cursor + offset
            tokens.append(
                Token(
                    span=Span(start, start + len(word)),
                    geometry=Geometry(
                        page_index=0,
                        bbox=BBox(0.1, round(0.05 * row, 4), 0.9, round(0.05 * row + 0.03, 4)),
                    ),
                )
            )
        cursor += len(line)

    return Document.create(
        text=DOCUMENT_TEXT,
        pages=pages,
        tokens=tuple(tokens),
        provenance=IngestProvenance(
            parser_id="example",
            parser_version="1.0.0",
            options={},
            options_hash=options_hash_for({}),
            capabilities=Capabilities(text=True, geometry=True, tables=False, handwriting=False),
            text_layer_used=True,
        ),
        source=BlobRef(
            blob_id=blob_id_for(DOCUMENT_TEXT.encode()),
            mime_type="application/pdf",
            size_bytes=len(DOCUMENT_TEXT.encode()),
            filename="invoice.pdf",
        ),
    )


def _value(path: str, payload: object, claim: str) -> ExtractedValue:
    return ExtractedValue(field_path=path, value=payload, present=True, claimed_text=claim)


def extraction_for(document: Document, *, stated_total: str) -> ExtractionResult:
    """What Milestone 3 would have produced, with the stated total varied.

    Both runs quote the document's own "1420.00" as the text the total was read
    from. In the second run the *value* disagrees with that text -- which is
    exactly the case Milestone 4 refused to judge, and this stage exists to.
    """
    values = {
        "number": _value("number", "INV-2026-001", "INV-2026-001"),
        "currency": _value("currency", "EUR", "EUR"),
        "total": _value("total", Decimal(stated_total), "1420.00"),
        "line_items": (
            {
                "description": _value("line_items[0].description", "Widget A", "Widget A"),
                "amount": _value("line_items[0].amount", Decimal("1000.00"), "1000.00"),
            },
            {
                "description": _value("line_items[1].description", "Widget B", "Widget B"),
                "amount": _value("line_items[1].amount", Decimal("420.00"), "420.00"),
            },
        ),
    }
    return ExtractionResult(
        values=values,
        artifact_id=options_hash_for({"example": "validate_invoice", "total": stated_total}),
        provenance=ExtractionProvenance(
            document_id=document.id,
            schema_identity=SCHEMA.identity,
            schema_hash=schema_hash_for(SCHEMA),
            prompt_hash=options_hash_for({"prompt": "example"}),
            projection_id="response-shape@1",
            adapter_id="example",
            adapter_version="1.0.0",
            model_id="example",
            model_version="1.0.0",
            decoding=ExtractionOptions(),
            extractor_version="1.0.0+example",
            usage=ModelUsage(),
        ),
    )


def report(document: Document, stated_total: str) -> None:
    extraction = extraction_for(document, stated_total=stated_total)
    grounding = ground(document, extraction)

    # Note what is *not* passed: the document. Everything a finding needs about
    # where a value sits was decided one stage earlier.
    result = validate(extraction, grounding, SCHEMA)

    print(f"\nstated total: {stated_total}")
    print(f"  verdict: {result.verdict}")
    print(
        f"  checks: {result.counts.declared} declared, "
        f"{result.counts.passed} passed, {result.counts.failed} failed, "
        f"{result.counts.not_evaluated} not evaluated"
    )
    for finding in result.findings:
        print(f"  [{finding.severity}] {finding.field_path}: {finding.reason}")
        print(f"      expected {finding.expected}, got {finding.actual}")
        print(f"      participants: {', '.join(finding.participants)}")
        if finding.span is not None:
            page = finding.pages[0] if finding.pages else "?"
            quoted = document.text[finding.span.start : finding.span.end]
            print(f"      found on page {page} at {finding.span}, reading {quoted!r}")
            if finding.geometry:
                box = finding.geometry[0].bbox
                print(f"      box: ({box.x0:.2f}, {box.y0:.2f}) - ({box.x1:.2f}, {box.y1:.2f})")


def main() -> None:
    document = build_document()

    # 1420.00 is what the lines add up to, and what the page says.
    report(document, "1420.00")

    # 1240.00 is a transposition a human makes and a model repeats. Every field is
    # still well formed; only the arithmetic disagrees.
    report(document, "1240.00")

    print(
        "\nThe second run is rejected, and the finding points at the place on the page\n"
        "where the total was read from -- so a reviewer is shown the evidence rather\n"
        "than told that a number is wrong."
    )

    sound = extraction_for(document, stated_total="1420.00")
    verdict = validate(sound, ground(document, sound), SCHEMA)
    assert verdict.verdict is Verdict.VALID
    assert not verdict.findings_at(Severity.ERROR)


if __name__ == "__main__":
    main()
