"""Native PDF text parser, over PDFium (``pypdfium2``).

The default path: fast, offline, free, and the one every contributor can run
(Principle V, ADR-0001, ADR-0025).

**Licensing.** ``pypdfium2`` is Apache-2.0/BSD-3 and PDFium is BSD-3, so this
path carries no obligation beyond docdoc's own Apache-2.0 and ships in the
default image. It replaced PyMuPDF (AGPL-3.0) in ADR-0025.

**Reading order.** Declared as ``pdfium-stream@1``: the order the PDF content
stream emits. PDFium's text page keeps that order on an upright page; on a
rotated one it reorders, so the rotation is reset to 0 in memory before the
text is read. This adapter claims no layout reconstruction. Reading order is a
declared property, not a promise about visual order (R5, FR-036).

**Rotation.** Text is read in *unrotated* page space, while the page is
displayed rotated. Normalizing one against the other would place every box
wrongly on a rotated page, so each box goes through ``displayed_box`` first
(R8, FR-006).

**Threads.** PDFium is not thread-safe; every call holds ``PDFIUM_LOCK``.
"""

from __future__ import annotations

import unicodedata
from typing import TYPE_CHECKING, Any, Final

import pypdfium2
import pypdfium2.version

from docdoc.ingest.capabilities import ParserCapabilities
from docdoc.ingest.errors import ParserError, UnsupportedDocumentError
from docdoc.ingest.normalize import DocumentBuilder, normalize_bbox
from docdoc.ingest.options import options_fingerprint
from docdoc.ingest.pdfium_lock import PDFIUM_LOCK
from docdoc.ingest.source import PDF
from docdoc.kernel import BBox, DocdocError, Document, GeometryError, IngestProvenance

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from docdoc.ingest.options import TransportSettings
    from docdoc.ingest.source import SourceFile
    from docdoc.kernel import TextLayerRecord

__all__ = [
    "PdfTextParser",
    "displayed_box",
    "displayed_size",
    "group_words",
    "page_text_lengths",
]

PARSER_ID: Final = "pdf-text"
ADAPTER_VERSION: Final = "2.0.0"

#: ``(left, bottom, right, top)`` in PDF user space, or ``(x0, y0, x1, y1)``.
Box = tuple[float, float, float, float]

#: Unicode categories that are never part of a word: controls, format
#: characters, surrogates, private use, unassigned. PDFium emits some of these
#: itself (``\x02`` for a soft hyphen at a line end, ``\ufffe``).
_NOT_TEXT: Final = frozenset({"Cc", "Cf", "Cs", "Co", "Cn"})


def displayed_size(crop: Box, rotation: int) -> tuple[float, float]:
    """The page's ``(width, height)`` as displayed."""
    left, bottom, right, top = crop
    width, height = right - left, top - bottom
    return (height, width) if rotation in (90, 270) else (width, height)


def displayed_box(box: Box, *, crop: Box, rotation: int) -> Box:
    """Map a box in PDF user space to ``(x0, y0, x1, y1)`` in points, origin at
    the top left of the crop box *as displayed* after ``rotation`` (clockwise)."""
    crop_left, crop_bottom, crop_right, crop_top = crop
    width, height = crop_right - crop_left, crop_top - crop_bottom
    left, bottom, right, top = box
    x0, x1 = left - crop_left, right - crop_left
    y0, y1 = crop_top - top, crop_top - bottom
    if rotation == 0:
        return (x0, y0, x1, y1)
    if rotation == 90:
        return (height - y1, x0, height - y0, x1)
    if rotation == 180:
        return (width - x1, height - y1, width - x0, height - y0)
    if rotation == 270:
        return (y0, width - x1, y1, width - x0)
    raise ValueError(f"rotation {rotation} is not a quarter turn")


def group_words(chars: Iterable[tuple[str, Box]]) -> list[list[tuple[str, Box]]]:
    """Group PDFium's characters, in text-page order, into lines of words.

    Whitespace and non-text characters end a word; a newline also ends the line.
    A word's box is the union of its characters' boxes. Blank lines are dropped.
    """
    lines: list[list[tuple[str, Box]]] = []
    line: list[tuple[str, Box]] = []
    text = ""
    box: Box | None = None

    def end_word() -> None:
        nonlocal text, box
        if text and box is not None:
            line.append((text, box))
        text, box = "", None

    for char, char_box in chars:
        if not char or char.isspace() or unicodedata.category(char[0]) in _NOT_TEXT:
            end_word()
            if char == "\n":
                if line:
                    lines.append(line)
                line = []
            continue
        text += char
        box = (
            char_box
            if box is None
            else (
                min(box[0], char_box[0]),
                min(box[1], char_box[1]),
                max(box[2], char_box[2]),
                max(box[3], char_box[3]),
            )
        )
    end_word()
    if line:
        lines.append(line)
    return lines


def _open(source: SourceFile) -> pypdfium2.PdfDocument:
    """Open the PDF, translating every library failure into docdoc's model.

    A library exception must not cross this boundary (FR-025); an encrypted or
    truncated file is a typed, explicit refusal, never an empty document. The
    caller holds ``PDFIUM_LOCK``.
    """
    try:
        return pypdfium2.PdfDocument(source.data)
    except pypdfium2.PdfiumError as error:
        # PDFium leaves its last error in place when some loads fail (a
        # zero-page file, for one), so a password error after an encrypted file
        # is stale. Only a file that declares /Encrypt is called encrypted.
        code = getattr(error, "err_code", None)
        if code == pypdfium2.raw.FPDF_ERR_PASSWORD and b"/Encrypt" in source.data:
            raise UnsupportedDocumentError(
                "the PDF is password-protected; docdoc does not guess passwords",
                reason="encrypted",
                blob_id=source.blob_id,
                media_type=source.media_type,
                parser_id=PARSER_ID,
            ) from None
        raise UnsupportedDocumentError(
            "the file could not be opened as a PDF",
            reason="corrupt",
            blob_id=source.blob_id,
            media_type=source.media_type,
            parser_id=PARSER_ID,
        ) from error


def page_text_lengths(source: SourceFile) -> tuple[str, ...]:
    """The raw text of each page, for the text-layer assessment.

    Lives here rather than in ``assess`` because reading a PDF is this module's
    job and the assessment may not import a provider library (Principle IV).
    Returns the text itself, so the counting rule -- and therefore the verdict --
    stays entirely inside the assessment, where it is versioned.
    """
    with PDFIUM_LOCK:
        document = _open(source)
        try:
            return tuple(page.get_textpage().get_text_range() for page in document)
        except Exception as error:
            raise UnsupportedDocumentError(
                "the PDF could not be read",
                reason="corrupt",
                blob_id=source.blob_id,
                parser_id=PARSER_ID,
            ) from error
        finally:
            document.close()


class PdfTextParser:
    """Turns a text-bearing PDF into a Document, offline."""

    id: Final = PARSER_ID
    #: Adapter version plus the binding version, which pins the PDFium build. An
    #: upgrade that changes extraction changes document identity, which is the
    #: point (ING-9).
    version: Final = f"{ADAPTER_VERSION}+pypdfium2-{pypdfium2.version.PYPDFIUM_INFO}"
    capabilities: Final = ParserCapabilities(
        text=True,
        geometry=True,
        tables=False,
        handwriting=False,
        media_types=frozenset({PDF}),
        requires_network=False,
    )
    reading_order: Final = "pdfium-stream@1"

    def parse(
        self,
        source: SourceFile,
        options: Mapping[str, Any],
        transport: TransportSettings | None = None,
        text_layer: TextLayerRecord | None = None,
    ) -> Document:
        """Read the PDF and build the Document. ``transport`` is ignored."""
        materialized, options_hash = options_fingerprint(options)
        with PDFIUM_LOCK:
            document = _open(source)
            try:
                builder = self._read(document, source)
            except DocdocError:
                raise
            except Exception as error:
                raise ParserError(
                    "the PDF could not be mapped to a document",
                    reason="internal",
                    parser_id=self.id,
                    blob_id=source.blob_id,
                    detail=type(error).__name__,
                ) from error
            finally:
                document.close()

        provenance = IngestProvenance(
            parser_id=self.id,
            parser_version=self.version,
            options=materialized,
            options_hash=options_hash,
            capabilities=self.capabilities.to_kernel(),
            text_layer_used=True,
            text_layer=text_layer,
            reading_order=self.reading_order,
        )
        return builder.build(source=source.blob_ref(), provenance=provenance)

    def _read(self, document: pypdfium2.PdfDocument, source: SourceFile) -> DocumentBuilder:
        builder = DocumentBuilder(geometry=True)

        for index, page in enumerate(document):
            rotation = page.get_rotation()
            crop: Box = tuple(page.get_cropbox())
            width, height = displayed_size(crop, rotation)
            builder.start_page(width=width, height=height, rotation=rotation)

            # In memory only: with the rotation in place PDFium's text page
            # reorders a rotated page, and stream order is what is declared.
            page.set_rotation(0)
            try:
                textpage = page.get_textpage()
                chars = [
                    (textpage.get_text_range(i, 1), textpage.get_charbox(i, loose=True))
                    for i in range(textpage.count_chars())
                ]
            except Exception as error:
                raise ParserError(
                    f"page {index} could not be read",
                    reason="internal",
                    parser_id=self.id,
                    blob_id=source.blob_id,
                ) from error

            for line in group_words(chars):
                builder.add_line(
                    [
                        (word, self._box(box, crop, rotation, width, height, index, source))
                        for word, box in line
                    ]
                )

        return builder

    def _box(
        self,
        box: Box,
        crop: Box,
        rotation: int,
        width: float,
        height: float,
        page_index: int,
        source: SourceFile,
    ) -> BBox:
        try:
            x0, y0, x1, y1 = displayed_box(box, crop=crop, rotation=rotation)
        except ValueError as error:
            raise ParserError(
                f"page {page_index} has a rotation that is not a quarter turn",
                reason="internal",
                parser_id=self.id,
                blob_id=source.blob_id,
                detail="rotation",
            ) from error
        try:
            return normalize_bbox(x0, y0, x1, y1, width=width, height=height, page_index=page_index)
        except GeometryError as error:
            # Surfaces as a parser fault rather than an anonymous geometry
            # error, because the parser is what produced the coordinate.
            raise ParserError(
                f"page {page_index} produced geometry outside the page",
                reason="internal",
                parser_id=self.id,
                blob_id=source.blob_id,
                detail=str(error),
            ) from error
