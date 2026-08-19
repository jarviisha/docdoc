"""Extraction/grounding pairs to validate, built offline from a hand-typed invoice.

The grounding half is produced by the **real grounder**, not hand-written. A
hand-written grounding result could claim a location the grounding stage would
never have produced, and every assertion about copied spans would then be
checking the fixture rather than the code.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

from tests.fixtures.validation.schemas import invoice_schema
from tests.support import make_document, make_extracted, make_extraction

from docdoc.extraction.identity import schema_hash_for
from docdoc.extraction.schema import Schema
from docdoc.extraction.value import ExtractedValue
from docdoc.grounding import ground
from docdoc.kernel import options_hash_for

__all__ = ["DOCUMENT_TEXT", "Pair", "build"]

#: One page of invoice, typed so that every claim below appears verbatim in it.
#: The stated total is deliberately not part of the arithmetic: `build` sets it,
#: so the same document serves the sound case and the line-short one.
DOCUMENT_TEXT = """ACME SUPPLIES LIMITED
VAT GB123456789

Invoice INV-2026-001
Issued 2026-01-15    Due 2026-02-14

Widget A    2 x 500.00    1000.00
Widget B    1 x 420.00     420.00

Total EUR 1420.00
Paid in full. Thank you for your business.
"""


class Pair:
    """A document, an extraction result, its grounding, and the schema."""

    __slots__ = ("document", "extraction", "grounding", "schema")

    def __init__(self, document: Any, extraction: Any, grounding: Any, schema: Schema) -> None:
        self.document = document
        self.extraction = extraction
        self.grounding = grounding
        self.schema = schema


def build(
    *,
    schema: Schema | None = None,
    total: str = "1420.00",
    total_claim: str | None = None,
    number: str = "INV-2026-001",
    currency: str = "EUR",
    due: str | None = "2026-02-14",
    notes_absent: bool = True,
    tax_id: str | None = "GB123456789",
    ungrounded_total: bool = False,
    extraction_overrides: dict[str, Any] | None = None,
    schema_hash: str | None = None,
) -> Pair:
    """One consistent (document, extraction, grounding, schema) set.

    Defaults produce the sound case: the stated total is 1420.00 and the two
    lines sum to exactly that. Pass ``total="1240.00"`` for the line-short case
    the spec uses as its example — a document where every field is individually
    well formed and the arithmetic is wrong.
    """
    schema = schema or invoice_schema()
    document = make_document(DOCUMENT_TEXT)

    values: dict[str, Any] = {
        "number": make_extracted("number", value=number, claimed_text=number),
        "currency": make_extracted("currency", value=currency, claimed_text=currency),
        "issue_date": make_extracted(
            "issue_date", value=date(2026, 1, 15), claimed_text="2026-01-15"
        ),
        "due_date": (
            make_extracted("due_date", value=date.fromisoformat(due), claimed_text=due)
            if due is not None
            else make_extracted("due_date", present=False)
        ),
        "total": make_extracted(
            "total",
            value=Decimal(total),
            # An ungrounded total is produced by claiming text the document does
            # not contain, which is what the model does when it fabricates.
            claimed_text=("999999.99" if ungrounded_total else (total_claim or total)),
        ),
        "notes": (
            make_extracted("notes", present=False)
            if notes_absent
            else make_extracted("notes", value="Paid in full.", claimed_text="Paid in full.")
        ),
        "supplier": {
            "name": make_extracted(
                "supplier.name",
                value="ACME SUPPLIES LIMITED",
                claimed_text="ACME SUPPLIES LIMITED",
            ),
            "tax_id": (
                make_extracted("supplier.tax_id", value=tax_id, claimed_text=tax_id)
                if tax_id is not None
                else make_extracted("supplier.tax_id", present=False)
            ),
        },
        "line_items": (
            _line(0, "Widget A", 2, "500.00", "1000.00"),
            _line(1, "Widget B", 1, "420.00", "420.00"),
        ),
    }
    if extraction_overrides:
        values.update(extraction_overrides)

    extraction = make_extraction(
        values,
        document=document,
        schema_identity=schema.identity,
        schema_hash=schema_hash or schema_hash_for(schema),
        # Derived from the content, as a real extraction artifact id is. A fixed
        # id would make two different fixtures indistinguishable, and the
        # mismatched-artifact refusal would then be untestable — it would pass
        # every pairing.
        artifact_id=options_hash_for(
            {
                "fixture": "validation",
                "schema": schema.identity,
                # The schema *hash*, not only the identity, exactly as the real
                # extract stage folds it (ADR-0008). Leaving it out would break
                # the chain in the fixture and hide the fact that editing a rule
                # invalidates everything downstream of extraction.
                "schema_hash": schema_hash or schema_hash_for(schema),
                "claims": sorted(value.claimed_text or "" for value in _every_value(values)),
                "values": sorted(str(value.value) for value in _every_value(values)),
            }
        ),
    )
    return Pair(document, extraction, ground(document, extraction), schema)


def _every_value(node: Any):
    """Every ExtractedValue in a value tree, at any depth."""
    if isinstance(node, ExtractedValue):
        yield node
    elif isinstance(node, dict):
        for child in node.values():
            yield from _every_value(child)
    elif isinstance(node, tuple):
        for child in node:
            yield from _every_value(child)


def _line(index: int, description: str, quantity: int, price: str, amount: str) -> dict[str, Any]:
    prefix = f"line_items[{index}]."
    return {
        "description": make_extracted(
            f"{prefix}description", value=description, claimed_text=description
        ),
        "quantity": make_extracted(f"{prefix}quantity", value=quantity, claimed_text=str(quantity)),
        "unit_price": make_extracted(
            f"{prefix}unit_price", value=Decimal(price), claimed_text=price
        ),
        "amount": make_extracted(f"{prefix}amount", value=Decimal(amount), claimed_text=amount),
    }
