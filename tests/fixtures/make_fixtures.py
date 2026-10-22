"""Generate the committed test fixtures.

Run with ``uv run python tests/fixtures/make_fixtures.py``. The outputs are
committed, so this script exists for reproducibility and review, not to run in
CI: a reviewer can see exactly what each fixture contains without opening a
binary.

Every document here is synthetic. No real document content, and nothing that
could carry PII, ever enters the repository (Constitution §Security).
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pypdfium2
from reportlab.lib import pdfencrypt
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen import canvas

HERE = Path(__file__).parent
PDF_DIR = HERE / "pdf"
IMAGE_DIR = HERE / "image"

# A4 at 72 dpi.
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


#: One page: lines of ``(text, font)`` drawn from the top, or a PNG covering it.
Page = list[str] | bytes


def _canvas(target: Path | io.BytesIO, **kwargs: object) -> canvas.Canvas:
    # `invariant=1` drops the timestamp and random file id, so a regeneration
    # with no input change writes the same bytes.
    filename = str(target) if isinstance(target, Path) else target
    return canvas.Canvas(filename, pagesize=(WIDTH, HEIGHT), invariant=1, **kwargs)  # type: ignore[arg-type]


def _write_lines(
    page: canvas.Canvas, lines: list[str], *, x: float = 60.0, y: float = 80.0
) -> None:
    """``y`` is the first baseline measured from the top, 18 pt apart."""
    page.setFont("Helvetica", 11)
    for offset, line in enumerate(lines):
        if line:
            page.drawString(x, HEIGHT - (y + offset * 18.0), line)


def _draw_image(page: canvas.Canvas, png: bytes) -> None:
    page.drawImage(ImageReader(io.BytesIO(png)), 0, 0, width=WIDTH, height=HEIGHT)


def _text_pages(pages: list[list[str]]) -> bytes:
    buffer = io.BytesIO()
    doc = _canvas(buffer)
    for lines in pages:
        _write_lines(doc, lines)
        doc.showPage()
    doc.save()
    return buffer.getvalue()


def _rasterize(pdf: bytes, *, dpi: int = 110) -> list[bytes]:
    """Render each page to PNG bytes, discarding the text layer."""
    document = pypdfium2.PdfDocument(pdf)
    try:
        images = []
        for page in document:
            buffer = io.BytesIO()
            page.render(scale=dpi / 72).to_pil().save(buffer, format="PNG")
            images.append(buffer.getvalue())
        return images
    finally:
        document.close()


def _save(path: Path, pages: list[Page], **kwargs: object) -> None:
    """Write ``pages`` in order: a list of lines is a text page, bytes a scan."""
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = _canvas(path, **kwargs)
    for page in pages:
        if isinstance(page, bytes):
            _draw_image(doc, page)
        else:
            _write_lines(doc, page)
        doc.showPage()
    doc.save()


def digital_invoice() -> None:
    """A page with a healthy text layer. The default path's happy case."""
    _save(PDF_DIR / "digital_invoice.pdf", [INVOICE_LINES])


def scanned_contract() -> None:
    """Two pages of pure image. No extractable text at all."""
    source = _text_pages(
        [CONTRACT_LINES, ["Schedule A", "", "Widgets, assorted sizes, as ordered."]]
    )
    _save(PDF_DIR / "scanned_contract.pdf", list(_rasterize(source)))


def sparse_text_layer() -> None:
    """A scan carrying page furniture only — the case a naive 'any text at all'
    rule gets wrong. Well under the 100-character threshold."""
    (scan,) = _rasterize(_text_pages([CONTRACT_LINES]))
    path = PDF_DIR / "sparse_text_layer.pdf"
    doc = _canvas(path)
    _draw_image(doc, scan)
    doc.setFont("Helvetica", 8)
    doc.drawString(520.0, HEIGHT - 800.0, "Page 1 of 1")
    doc.showPage()
    doc.save()


def mixed_pages() -> None:
    """Two digital pages and one scanned page. Routing is whole-document, so the
    scanned page yields no tokens — and the per-page verdict records why."""
    (scan,) = _rasterize(_text_pages([["Signed copy", "", "Received and acknowledged."]]))
    _save(PDF_DIR / "mixed_pages.pdf", [INVOICE_LINES, CONTRACT_LINES, scan])


def two_column() -> None:
    """Two text columns, for pinning the parser's declared reading order."""
    path = PDF_DIR / "two_column.pdf"
    doc = _canvas(path)
    _write_lines(doc, LEFT_COLUMN, x=55.0, y=90.0)
    _write_lines(doc, RIGHT_COLUMN, x=320.0, y=90.0)
    doc.showPage()
    doc.save()


def rotated_90() -> None:
    """A page the file rotates for display. Geometry must describe the page as
    displayed, not in unrotated file space."""
    path = PDF_DIR / "rotated_90.pdf"
    doc = _canvas(path)
    _write_lines(doc, INVOICE_LINES)
    doc.setPageRotation(90)
    # reportlab writes the MediaBox swapped for a quarter-turned page, so the
    # size is handed over pre-swapped: the file then says "an upright A4 page,
    # displayed rotated", which is what the text above was drawn on.
    doc.setPageSize((HEIGHT, WIDTH))
    doc.showPage()
    doc.save()


def encrypted() -> None:
    """Password-protected. Must fail explicitly, never as an empty document.

    128-bit, not 256: reportlab's AES-256 needs `pyaes`, and the test needs a
    password-protected file, not a particular cipher."""
    _save(
        PDF_DIR / "encrypted.pdf",
        [INVOICE_LINES],
        encrypt=pdfencrypt.StandardEncryption("user-secret", "owner-secret", strength=128),
    )


# Every character here was checked to round-trip through reportlab and back out of
# PDFium with the fonts available in this repository. Notably absent: the
# euro sign, en and em dashes, ligatures, combining marks, right-to-left script,
# and anything outside the BMP -- all of them come back as a replacement glyph or
# vanish, so a fixture containing them would assert nothing. See the docstring on
# `unicode_text` for where those are covered instead.
UNICODE_LINES = [
    ("FACTURE \u53d1\u7968 num\u00e9ro INV-001", "STSong-Light"),
    ("Client: Soci\u00e9t\u00e9 G\u00e9n\u00e9rale du Nord-Est", "Helvetica"),
    ("Adresse: 12 rue de l'\u00c9glise, Ch\u00e2teau-Th\u00e9baud", "Helvetica"),
    ("R\u00e9f\u00e9rence de paiement: FR76 3000 4000 0100", "Helvetica"),
    ("Description                Qt\u00e9      Prix      Montant", "Helvetica"),
    ("Widget, grand mod\u00e8le        4     25,00     100,00", "Helvetica"),
    ("Widget, petit mod\u00e8le       10      7,50      75,00", "Helvetica"),
    ("Livraison, exp\u00e9di\u00e9 le 14/03    1     15,00      15,00", "Helvetica"),
    ("Sous-total                                  190,00", "Helvetica"),
    ("TVA 20 %                                     38,00", "Helvetica"),
    ("Total \u00e0 payer                               228,00 \u00a3", "Helvetica"),
    (
        "\u91d1\u989d\u5408\u8ba1 \u5ba2\u6237 \u5730\u5740 \u65e5\u671f \u4ed8\u6b3e \u6761\u4ef6",
        "STSong-Light",
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
    # A CID font that every PDF reader carries, so nothing is embedded.
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    path = PDF_DIR / "unicode_text.pdf"
    doc = _canvas(path)
    for offset, (line, font) in enumerate(UNICODE_LINES):
        doc.setFont(font, 11)
        doc.drawString(60.0, HEIGHT - (80.0 + offset * 20.0), line)
    doc.showPage()
    doc.save()


def zero_pages() -> None:
    """A PDF with no pages at all.

    Hand-written, because PDF libraries refuse to save one. The spec lists it as
    an edge case, and the only way to have it is to write the bytes.
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
    (png,) = _rasterize(_text_pages([INVOICE_LINES]))
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
