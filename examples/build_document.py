"""Build a document by hand and trace a value back to the page it came from.

Runs standalone with no database, no object storage, no credentials, and no
network access:

    uv run python examples/build_document.py

This is the smallest complete demonstration of what docdoc's kernel is for --
an extracted value that can always answer "where did this come from?".
"""

from __future__ import annotations

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

# In a real pipeline these bytes come from a file and the tokens come from a
# parser. Here we write both out by hand so the example needs nothing external.
RAW = b"(pretend this is a PDF)"
TEXT = "Invoice No: INV-001\nVendor: ABC Trading\nTotal: 125000"


def token(start: int, end: int, page: int, x0: float, y0: float) -> Token:
    return Token(
        span=Span(start, end),
        geometry=Geometry(page, BBox(x0, y0, round(x0 + 0.18, 4), round(y0 + 0.03, 4))),
        source_confidence=0.99,
    )


def build() -> Document:
    options: dict[str, object] = {"dpi": 300}
    return Document.create(
        text=TEXT,
        pages=(
            Page(index=0, span=Span(0, 19), width=612.0, height=792.0),
            Page(index=1, span=Span(19, len(TEXT)), width=612.0, height=792.0),
        ),
        tokens=(
            token(0, 7, 0, 0.10, 0.10),  # Invoice
            token(8, 11, 0, 0.30, 0.10),  # No:
            token(12, 19, 0, 0.45, 0.10),  # INV-001
            token(20, 27, 1, 0.10, 0.20),  # Vendor:
            token(28, 31, 1, 0.30, 0.20),  # ABC
            token(32, 39, 1, 0.42, 0.20),  # Trading
            token(40, 46, 1, 0.10, 0.30),  # Total:
            token(47, 53, 1, 0.30, 0.30),  # 125000
        ),
        provenance=IngestProvenance(
            parser_id="handwritten_example",
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
            filename="invoice.pdf",
        ),
    )


def trace(document: Document, value: str) -> None:
    """Print where a value came from -- the question docdoc exists to answer."""
    print(f"\n{value}")
    for span in document.find(value):
        for page_index in document.page_for(span):
            print(f"    └── Page {page_index + 1}")
        for geometry in document.locate(span):
            box = geometry.bbox
            print(
                f"        └── Bounding box on page {geometry.page_index + 1}: "
                f"({box.x0:.2f}, {box.y0:.2f}) → ({box.x1:.2f}, {box.y1:.2f})"
            )


def main() -> None:
    document = build()

    print("Document")
    print(f"    blob     {document.source.blob_id}")
    print(f"    parse    {document.id}")
    print(f"    parser   {document.provenance.parser_id} v{document.provenance.parser_version}")
    print(f"    pages    {len(document.pages)}   tokens {len(document.tokens)}")

    # The two identities are deliberately different: the same file parsed by a
    # different parser would share `blob` and get a different `parse` id, because
    # its text positions are not interchangeable with these (ADR-0002).
    assert document.id != document.source.blob_id

    for value in ("INV-001", "ABC Trading", "125000"):
        trace(document, value)

    # Cutting the document apart and reassembling it changes nothing about where
    # a value physically came from. This is the invariant the property suite
    # proves across thousands of generated cases.
    page_one = document.slice(document.pages[0].span)
    page_two = document.slice(document.pages[1].span)
    rebuilt = Document.merge((page_one, page_two))

    (original,) = document.find("125000")
    (recovered,) = rebuilt.find("125000")
    assert document.locate(original) == rebuilt.locate(recovered)
    print("\nSliced into 2 parts and reassembled: geometry unchanged. ✓")


if __name__ == "__main__":
    main()
