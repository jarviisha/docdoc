"""Take an extracted value to the place on the page it came from.

    uv run python examples/ground_invoice.py

No credentials, no network, no database. Unlike the extraction example, that is
not a convenience here -- grounding is deterministic code over data already in
memory, so there is nothing for a credential to unlock.

This is the milestone that makes docdoc's central claim demonstrable end to end.
Extraction records the text a model *claims* it read a value from; grounding
decides, by itself, whether that claim resolves to a real place in the document.
The model never gets a vote on whether its own output is grounded.
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

# This example prints a ligature -- U+FB01, the whole point of the supplier field
# below -- so it reconfigures its own stdout rather than assuming the console can
# encode it. A console's encoding follows the user's locale: Python defaults
# stdout to the ANSI code page on Windows, and an example that dies with
# UnicodeEncodeError before reaching the point it was written to make is worse
# than no example. `tests/integration/test_examples_run.py` runs every example
# under `PYTHONIOENCODING=ascii` precisely to force that failure on the cheapest
# machine rather than the slowest one, and names this as the correct remedy.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from docdoc.extraction import SchemaRegistry, extract
from docdoc.extraction.adapters.echo import EchoAdapter
from docdoc.grounding import ground

# The document below is typeset the way a real PDF is: the supplier's name carries
# an "fi" ligature, and the reference is broken across a line by a hyphen. A model
# reading it quotes what a human reads. Grounding still has to find it.
#
# The ligature is in a lowercase word on purpose. U+FB01 is the *lowercase* fi
# ligature and NFKC expands it to lowercase "fi", so a typesetter would never use
# it inside an all-caps word -- and if one did, the claim would differ in case and
# land at the fuzzy tier rather than the exact one.
DOCUMENT_TEXT = """\
Acme Ofﬁce Supplies Ltd
Invoice INV-\n2026-0042
Issued 1 March 2026

Widget, large     1,000.00
Delivery            240.00
TOTAL             1,240.00
"""

SCHEMA = {
    "name": "invoice",
    "version": 1,
    "fields": [
        {
            "name": "supplier",
            "type": "string",
            "required": True,
            "description": "The supplying company's name, exactly as printed.",
        },
        {
            "name": "invoice_number",
            "type": "string",
            "required": True,
            "description": "The invoice's own identifying number.",
        },
        {
            "name": "total",
            "type": "decimal",
            "required": True,
            "description": "The final amount payable.",
        },
        {
            "name": "purchase_order",
            "type": "string",
            "description": "The buyer's PO reference. Absent on many invoices.",
        },
    ],
}

PROMPT = """\
Return the fields the response schema declares. For each, give the typed `value` and the
`claimed_text` exactly as it appears in the document, character for character.
"""

#: What a model plausibly returns for the document above. Note that it quotes what
#: a *human* reads -- "OFFICE" without the ligature, the reference unbroken -- and
#: that `purchase_order` is honestly absent.
ANSWER = {
    "supplier": {"value": "Acme Office Supplies Ltd", "claimed_text": "Acme Office Supplies Ltd"},
    "invoice_number": {"value": "INV-2026-0042", "claimed_text": "INV-2026-0042"},
    "total": {"value": "1240.00", "claimed_text": "TOTAL             1,240.00"},
    "purchase_order": {"value": None, "claimed_text": None},
}


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "invoice@1.json").write_text(json.dumps(SCHEMA, indent=2))
        (root / "prompts").mkdir()
        (root / "prompts" / "invoice@1.md").write_text(PROMPT)

        registry = SchemaRegistry.from_paths([root])
        document = _document(DOCUMENT_TEXT)

        # 1. Extract. Every value comes back ungrounded -- Milestone 3 records the
        #    claim and deliberately resolves none of it.
        canned = {k: {**v, "confidence": None} for k, v in ANSWER.items()}
        extraction = extract(
            document,
            schema="invoice@1",
            registry=registry,
            adapter=EchoAdapter.returning("invoice@1", canned),
        )
        print(f"extracted, grounding = {extraction.value_at('total').grounding}\n")

        # 2. Ground. This is the whole feature.
        result = ground(document, extraction)

        print(f"{'field':16} {'status':11} {'score':>6}  where")
        print("-" * 68)
        for path in sorted(result.outcomes):
            outcome = result.outcomes[path]
            if outcome.span is None:
                print(f"{path:16} {outcome.status.value:11} {'--':>6}  --")
                continue
            box = outcome.geometry[0].bbox if outcome.geometry else None
            where = f"p{outcome.pages[0]} chars {outcome.span.start}-{outcome.span.end}"
            if box:
                where += f"  bbox({box.x0:.2f},{box.y0:.2f},{box.x1:.2f},{box.y1:.2f})"
            print(f"{path:16} {outcome.status.value:11} {outcome.score:6.3f}  {where}")

        # 3. Read the located text back out of the untouched source.
        supplier = result.outcomes["supplier"]
        assert supplier.span is not None
        located = document.text[supplier.span.start : supplier.span.end]
        print(f"\nsupplier resolved to: {located!r}")
        print("  Note the ligature. The model quoted 'Office'; the source carries a")
        print("  single U+FB01 glyph. It still resolves EXACTLY, because matching runs")
        print("  against a folded view -- and the range returned points at the")
        print("  untouched source, ligature intact.")

        # 4. The absence, which is not a failure.
        print(f"\npurchase_order in outcomes: {'purchase_order' in result.outcomes}")
        print("  The model said the document does not contain one. There is nothing")
        print("  to locate, so it gets no outcome and stays out of the rate:")
        print(f"    not_applicable = {result.counts.not_applicable}")

        # 5. The metric, computable without re-running anything.
        counts = result.counts
        print(f"\nexact={counts.exact} fuzzy={counts.fuzzy} ungrounded={counts.ungrounded}")
        print(f"grounding rate: {counts.grounding_rate:.0%}")

        # 6. Provenance: what would have to change for the answer to move.
        p = result.provenance
        print(f"\nartifact_id : {result.artifact_id}")
        print(f"  chained from extraction {p.extraction_artifact_id[:23]}...")
        print(f"  grounding_version={p.grounding_version}")
        print(f"  match_view_version={p.match_view_version}")
        print(f"  threshold={p.options.threshold}")


def _document(text: str):
    """A minimal `Document` with geometry, so `locate` has boxes to return.

    In real use this is `docdoc.ingest.parse(pdf_bytes)`. Built by hand here so
    the example needs no PDF fixture and no AGPL-licensed `docdoc[pdf]` extra --
    but *with* per-word geometry, because a grounding example that could not show
    a bounding box would be missing the point.
    """
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

    lines = text.split("\n")
    tokens = []
    offset = 0
    for row, line in enumerate(lines):
        column = 0
        for word in line.split(" "):
            if word:
                start = text.index(word, offset + column)
                # A crude but honest layout: x from the character column, y from
                # the line. Real geometry comes from the parser.
                x0 = min(0.98, 0.05 + (start - offset) / 90)
                y0 = min(0.98, 0.05 + row / 40)
                tokens.append(
                    Token(
                        Span(start, start + len(word)),
                        Geometry(0, BBox(x0, y0, min(1.0, x0 + len(word) / 90), y0 + 0.02)),
                        None,
                    )
                )
            column += len(word) + 1
        offset += len(line) + 1

    data = text.encode()
    return Document.create(
        text=text,
        pages=(Page(index=0, span=Span(0, len(text)), width=612.0, height=792.0),),
        tokens=tokens,
        provenance=IngestProvenance(
            parser_id="example",
            parser_version="1.0.0",
            options={},
            options_hash=options_hash_for({}),
            capabilities=Capabilities(text=True, geometry=True, tables=False, handwriting=False),
            text_layer_used=False,
        ),
        source=BlobRef(blob_id=blob_id_for(data), mime_type="text/plain", size_bytes=len(data)),
    )


if __name__ == "__main__":
    main()
