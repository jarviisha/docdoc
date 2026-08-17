"""Extract structured values from a document, with no credentials and no network.

    uv run python examples/extract_invoice.py

This runs after ``pip install docdoc`` as well as from a git checkout, which is why
it writes its own schema to a temporary directory instead of reading the
repository's ``schemas/``: that directory is data a *deployment* supplies and is
not packaged in the wheel. Writing the file also happens to be the clearest
demonstration available that a schema is data rather than code -- adding a document
type to docdoc means adding two files, not editing the engine.

The extraction here uses the in-repo ``EchoAdapter``, so it costs nothing and
returns the same answer every time. The commented line near the bottom is the only
change needed to reach a real model.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from docdoc.extraction import SchemaRegistry, extract
from docdoc.extraction.adapters.echo import EchoAdapter

# --------------------------------------------------------------------------
# 1. A schema. Data, not code -- this is Principle VI read literally.
# --------------------------------------------------------------------------

INVOICE_SCHEMA = {
    "name": "invoice",
    "version": 1,
    "fields": [
        {
            "name": "invoice_number",
            "type": "string",
            "required": True,
            "description": "The invoice's own identifying number, exactly as printed.",
        },
        {
            "name": "issue_date",
            "type": "date",
            "required": True,
            "description": "The date the invoice was issued, as an ISO-8601 date.",
        },
        {
            "name": "due_date",
            "type": "date",
            "description": (
                "The date payment is due. Absent on many invoices; leave it null rather "
                "than inferring one from payment terms."
            ),
        },
        {
            "name": "total",
            # `decimal`, not `number`. A total that has been through a float is not
            # the total that was printed.
            "type": "decimal",
            "required": True,
            "description": "The final amount payable, including tax.",
            # Declared here, enforced by the validation stage (Milestone 5). This
            # layer stores it and hashes it; it does not act on it.
            "constraints": {"minimum": 0},
        },
        {
            "name": "line_items",
            "cardinality": "repeating_group",
            "description": "One entry per charged line, in the order printed.",
            "fields": [
                {
                    "name": "description",
                    "type": "string",
                    "required": True,
                    "description": "What was supplied.",
                },
                {
                    "name": "amount",
                    "type": "decimal",
                    "required": True,
                    "description": "The line's total charge.",
                },
            ],
        },
    ],
}

PROMPT = """\
You are reading one invoice. Return the fields the response schema declares, and nothing else.

For every field return two things:

- `value` -- typed as the schema declares it. Use `null` when the document does not contain the
  field. `null` is a correct answer; a guess is not.
- `claimed_text` -- the text **exactly as it appears in the document**, character for character,
  that you read the value from. If the document says `1,240.00`, that is the claimed text even when
  the value is `1240.00`.

The claimed text is how a later stage locates the value on the page. Text you have altered cannot be
located, so an altered claim is worse than an absent one.
"""

DOCUMENT_TEXT = """\
ACME SUPPLIES LTD
14 Example Road, Bristol BS1 4ND

INVOICE   INV-2026-0042
Issued:   1 March 2026

Widget, large        2 x 500.00     1,000.00
Delivery             1 x 240.00       240.00
------------------------------------------------
TOTAL                              1,240.00
"""


def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        (root / "invoice@1.json").write_text(json.dumps(INVOICE_SCHEMA, indent=2))
        (root / "prompts").mkdir()
        (root / "prompts" / "invoice@1.md").write_text(PROMPT)

        # ------------------------------------------------------------------
        # 2. Register it. Concrete versions only -- there is no `latest`.
        # ------------------------------------------------------------------
        registry = SchemaRegistry.from_paths([root])
        print(f"registered: {registry.identities()}")

        entry = registry.resolve("invoice@1")
        print(f"schema_hash: {entry.schema_hash}")
        print("  the version answers 'did the contract change?';")
        print("  the hash answers 'did anything result-affecting change?'\n")

        # ------------------------------------------------------------------
        # 3. A document. Normally this comes from docdoc.ingest.parse(pdf_bytes);
        #    built by hand here so the example needs no PDF and no PyMuPDF.
        # ------------------------------------------------------------------
        document = _document(DOCUMENT_TEXT)

        # ------------------------------------------------------------------
        # 4. Extract. Swap the adapter for a real one and nothing else changes:
        #
        #        from docdoc.extraction.adapters.gemini import GeminiAdapter
        #        adapter = GeminiAdapter()      # needs GEMINI_API_KEY, costs money
        # ------------------------------------------------------------------
        adapter = EchoAdapter.returning("invoice@1", _CANNED_ANSWER)
        result = extract(document, schema="invoice@1", registry=registry, adapter=adapter)

        print(f"invoice_number : {result.value_at('invoice_number').value!r}")
        print(f"issue_date     : {result.value_at('issue_date').value}")
        print(f"total          : {result.value_at('total').value!r}  <- a Decimal, not a float")
        print(f"  claimed text : {result.value_at('total').claimed_text!r}  <- byte-faithful")

        due = result.value_at("due_date")
        print(f"due_date       : present={due.present}, value={due.value}")
        print("  an absence is a recorded outcome, not a gap and not an error\n")

        for i, item in enumerate(result.values["line_items"]):
            print(f"line_items[{i}]   : {item['description'].value!r} = {item['amount'].value}")

        # ------------------------------------------------------------------
        # 5. Provenance. Every result explains itself.
        # ------------------------------------------------------------------
        p = result.provenance
        print(f"\nartifact_id    : {result.artifact_id}")
        print(f"  document     : {p.document_id}")
        print(f"  schema       : {p.schema_identity}  ({p.schema_hash[:23]}…)")
        print(f"  adapter      : {p.adapter_id} {p.adapter_version}")
        print(f"  extractor    : {p.extractor_version}")

        # ------------------------------------------------------------------
        # 6. What is deliberately NOT here.
        # ------------------------------------------------------------------
        total = result.value_at("total")
        print(f"\ngrounding      : {total.grounding}")
        print("  Unresolved, on purpose. Resolving the claimed text to a page and a")
        print("  bounding box is Milestone 4's stage, with its own artifact under")
        print("  ADR-0003. Until then every value is ungrounded.")
        print(f"model_confidence: {total.model_confidence}  <- UNTRUSTED, routes nothing")


def _document(text: str):
    """Build a minimal `Document` by hand.

    In real use this is `docdoc.ingest.parse(pdf_bytes, require=...)`. Constructing
    one directly keeps the example free of a PDF fixture and of the AGPL-licensed
    `docdoc[pdf]` extra.
    """
    from docdoc.kernel import (
        BlobRef,
        Capabilities,
        Document,
        IngestProvenance,
        Page,
        Span,
        blob_id_for,
        options_hash_for,
    )

    data = text.encode()
    return Document.create(
        text=text,
        pages=(Page(index=0, span=Span(0, len(text)), width=612.0, height=792.0),),
        tokens=(),
        provenance=IngestProvenance(
            parser_id="example",
            parser_version="1.0.0",
            options={},
            options_hash=options_hash_for({}),
            capabilities=Capabilities(text=True, geometry=False, tables=False, handwriting=False),
            # A hand-built document took no text-layer decision, and saying so is
            # the honest record rather than claiming one was made.
            text_layer_used=False,
        ),
        source=BlobRef(blob_id=blob_id_for(data), mime_type="text/plain", size_bytes=len(data)),
    )


def _sc(value: object, claimed: str | None) -> dict[str, object]:
    return {"value": value, "claimed_text": claimed, "confidence": None}


#: What a model would return. Canned here so the example is free and deterministic.
_CANNED_ANSWER = {
    "invoice_number": _sc("INV-2026-0042", "INV-2026-0042"),
    "issue_date": _sc("2026-03-01", "1 March 2026"),
    "due_date": _sc(None, None),
    "total": _sc("1240.00", "1,240.00"),
    "line_items": [
        {
            "description": _sc("Widget, large", "Widget, large"),
            "amount": _sc("1000.00", "1,000.00"),
        },
        {"description": _sc("Delivery", "Delivery"), "amount": _sc("240.00", "240.00")},
    ],
}


if __name__ == "__main__":
    main()
