"""Generate the committed test fixtures.

Run with ``uv run python tests/fixtures/make_fixtures.py``. The outputs are
committed, so this script exists for reproducibility and review, not to run in
CI: a reviewer can see exactly what each fixture contains without opening a
binary.

Every document here is synthetic. No real document content, and nothing that
could carry PII, ever enters the repository (Constitution §Security).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).parent
PDF_DIR = HERE / "pdf"
IMAGE_DIR = HERE / "image"

# A4 at 72 dpi, the default PyMuPDF page size.
WIDTH, HEIGHT = 595.0, 842.0

# Deliberately more than 100 characters per page, so `text-layer@1` judges these
# pages text-bearing by a comfortable margin rather than by a hair.
INVOICE_LINES = [
    "ACME SUPPLIES LIMITED",
    "Invoice INV-001",
    "Issued 2026-03-14   Due 2026-04-13",
    "Bill to: Northwind Trading Company",
    "12 Harbour Road, Port Meadow",
    "",
    "Description                Qty     Unit      Amount",
    "Widget, large                4     25.00     100.00",
    "Widget, small               10      7.50      75.00",
    "Delivery                     1     15.00      15.00",
    "",
    "Subtotal                                     190.00",
    "VAT 20%                                       38.00",
    "Total                                        228.00",
]

CONTRACT_LINES = [
    "SERVICE AGREEMENT",
    "",
    "This agreement is made between Acme Supplies Limited and",
    "Northwind Trading Company on the fourteenth of March, 2026.",
    "",
    "1. The supplier shall deliver the goods described in Schedule A",
    "   within thirty days of the order being accepted.",
    "2. The customer shall pay each invoice within thirty days.",
    "3. Either party may terminate on sixty days written notice.",
]

LEFT_COLUMN = [
    "The first column begins here and",
    "continues for several lines so that",
    "a reading order that runs across the",
    "page rather than down it is obvious",
    "when the tokens are compared.",
]

RIGHT_COLUMN = [
    "The second column begins here and",
    "should appear after every line of",
    "the first column, not interleaved",
    "with it, when the parser declares a",
    "layout-aware reading order.",
]


def _write_lines(page: pymupdf.Page, lines: list[str], *, x: float = 60.0, y: float = 80.0) -> None:
    for offset, line in enumerate(lines):
        if line:
            page.insert_text((x, y + offset * 18.0), line, fontsize=11)


def _text_page_document(lines: list[str]) -> pymupdf.Document:
    doc = pymupdf.open()
    page = doc.new_page(width=WIDTH, height=HEIGHT)
    _write_lines(page, lines)
    return doc


def _rasterize(doc: pymupdf.Document, *, dpi: int = 110) -> list[bytes]:
    """Render each page to PNG bytes, discarding the text layer."""
    return [page.get_pixmap(dpi=dpi).tobytes("png") for page in doc]


def _image_only_document(page_images: list[bytes]) -> pymupdf.Document:
    out = pymupdf.open()
    for png in page_images:
        page = out.new_page(width=WIDTH, height=HEIGHT)
        page.insert_image(pymupdf.Rect(0, 0, WIDTH, HEIGHT), stream=png)
    return out


def _save(doc: pymupdf.Document, path: Path, **kwargs: object) -> None:
    doc.set_metadata({})  # keep fixtures free of tool and timestamp noise
    path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(str(path), garbage=4, deflate=True, **kwargs)
    doc.close()


def digital_invoice() -> None:
    """A page with a healthy text layer. The default path's happy case."""
    _save(_text_page_document(INVOICE_LINES), PDF_DIR / "digital_invoice.pdf")


def scanned_contract() -> None:
    """Two pages of pure image. No extractable text at all."""
    source = _text_page_document(CONTRACT_LINES)
    source.new_page(width=WIDTH, height=HEIGHT)
    _write_lines(source[1], ["Schedule A", "", "Widgets, assorted sizes, as ordered."])
    _save(_image_only_document(_rasterize(source)), PDF_DIR / "scanned_contract.pdf")
    source.close()


def sparse_text_layer() -> None:
    """A scan carrying page furniture only — the case a naive 'any text at all'
    rule gets wrong. Well under the 100-character threshold."""
    source = _text_page_document(CONTRACT_LINES)
    doc = _image_only_document(_rasterize(source))
    source.close()
    doc[0].insert_text((520.0, 800.0), "Page 1 of 1", fontsize=8)
    _save(doc, PDF_DIR / "sparse_text_layer.pdf")


def mixed_pages() -> None:
    """Two digital pages and one scanned page. Routing is whole-document, so the
    scanned page yields no tokens — and the per-page verdict records why."""
    digital = _text_page_document(INVOICE_LINES)
    digital.new_page(width=WIDTH, height=HEIGHT)
    _write_lines(digital[1], CONTRACT_LINES)

    scan_source = _text_page_document(["Signed copy", "", "Received and acknowledged."])
    scan = _image_only_document(_rasterize(scan_source))
    scan_source.close()

    digital.insert_pdf(scan)
    scan.close()
    _save(digital, PDF_DIR / "mixed_pages.pdf")


def two_column() -> None:
    """Two text columns, for pinning the parser's declared reading order."""
    doc = pymupdf.open()
    page = doc.new_page(width=WIDTH, height=HEIGHT)
    _write_lines(page, LEFT_COLUMN, x=55.0, y=90.0)
    _write_lines(page, RIGHT_COLUMN, x=320.0, y=90.0)
    _save(doc, PDF_DIR / "two_column.pdf")


def rotated_90() -> None:
    """A page the file rotates for display. Geometry must describe the page as
    displayed, not in unrotated file space."""
    doc = _text_page_document(INVOICE_LINES)
    doc[0].set_rotation(90)
    _save(doc, PDF_DIR / "rotated_90.pdf")


def encrypted() -> None:
    """Password-protected. Must fail explicitly, never as an empty document."""
    _save(
        _text_page_document(INVOICE_LINES),
        PDF_DIR / "encrypted.pdf",
        encryption=pymupdf.PDF_ENCRYPT_AES_256,
        owner_pw="owner-secret",
        user_pw="user-secret",
    )


# Every character here was checked to round-trip through insert_text and back out
# of get_text with the fonts available in this repository. Notably absent: the
# euro sign, en and em dashes, ligatures, combining marks, right-to-left script,
# and anything outside the BMP -- all of them come back as a replacement glyph or
# vanish, so a fixture containing them would assert nothing. See the docstring on
# `unicode_text` for where those are covered instead.
UNICODE_LINES = [
    ("FACTURE \u53d1\u7968 num\u00e9ro INV-001", "china-s"),
    ("Client: Soci\u00e9t\u00e9 G\u00e9n\u00e9rale du Nord-Est", "helv"),
    ("Adresse: 12 rue de l'\u00c9glise, Ch\u00e2teau-Th\u00e9baud", "helv"),
    ("R\u00e9f\u00e9rence de paiement: FR76 3000 4000 0100", "helv"),
    ("Description                Qt\u00e9      Prix      Montant", "helv"),
    ("Widget, grand mod\u00e8le        4     25,00     100,00", "helv"),
    ("Widget, petit mod\u00e8le       10      7,50      75,00", "helv"),
    ("Livraison, exp\u00e9di\u00e9 le 14/03    1     15,00      15,00", "helv"),
    ("Sous-total                                  190,00", "helv"),
    ("TVA 20 %                                     38,00", "helv"),
    ("Total \u00e0 payer                               228,00 \u00a3", "helv"),
    (
        "\u91d1\u989d\u5408\u8ba1 \u5ba2\u6237 \u5730\u5740 \u65e5\u671f \u4ed8\u6b3e \u6761\u4ef6",
        "china-s",
    ),
]


def unicode_text() -> None:
    """Multi-byte characters through the whole native path.

    Deliberately limited to what the toolchain can actually embed. Measured with
    the fonts available here: precomposed Latin accents and CJK round-trip
    exactly; combining marks, ligatures, right-to-left script, and characters
    outside the BMP come back as replacement glyphs or vanish, because no font in
    this repository carries them.

    Committing a fixture that *claims* to cover those would be worse than not
    having one -- the test would pass while asserting nothing. They are covered at
    the builder level instead, in tests/unit/test_unicode_text.py, where no font
    is involved and the offsets are the whole point.
    """
    doc = pymupdf.open()
    page = doc.new_page(width=WIDTH, height=HEIGHT)
    for offset, (line, font) in enumerate(UNICODE_LINES):
        page.insert_text((60.0, 80.0 + offset * 20.0), line, fontsize=11, fontname=font)
    _save(doc, PDF_DIR / "unicode_text.pdf")


def zero_pages() -> None:
    """A PDF with no pages at all.

    Hand-written, because PyMuPDF refuses to save one ("cannot save with zero
    pages"). The spec lists it as an edge case, and the only way to have it is to
    write the bytes.
    """
    body = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
        b"2 0 obj\n<< /Type /Pages /Kids [] /Count 0 >>\nendobj\n"
    )
    offsets = [body.index(b"1 0 obj"), body.index(b"2 0 obj")]
    xref_at = len(body)
    trailer = (
        b"xref\n0 3\n0000000000 65535 f \n"
        + b"".join(b"%010d 00000 n \n" % offset for offset in offsets)
        + b"trailer\n<< /Size 3 /Root 1 0 R >>\nstartxref\n"
        + str(xref_at).encode()
        + b"\n%%EOF\n"
    )
    (PDF_DIR / "zero_pages.pdf").write_bytes(body + trailer)


def sample_image() -> None:
    """A single-page image. Required by SC-004's sample set and by ING-13."""
    source = _text_page_document(INVOICE_LINES)
    png = source[0].get_pixmap(dpi=110).tobytes("png")
    source.close()
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    (IMAGE_DIR / "sample_page.png").write_bytes(png)


def main() -> int:
    for build in (
        digital_invoice,
        scanned_contract,
        sparse_text_layer,
        mixed_pages,
        two_column,
        rotated_90,
        encrypted,
        unicode_text,
        zero_pages,
        sample_image,
    ):
        build()
        print(f"built {build.__name__}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
