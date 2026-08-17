"""Native PDF text parser, over PyMuPDF.

The default path: fast, offline, free, and the one every contributor can run
(Principle V, ADR-0001).

**Licensing.** PyMuPDF is AGPL-3.0 (or a paid commercial licence) while docdoc is
Apache-2.0. That is why this adapter lives behind the opt-in ``docdoc[pdf]``
extra: docdoc's own distribution stays Apache-2.0, and the obligation is incurred
only by a user who installs the extra. See research.md R1.

**Reading order.** Declared as ``pymupdf-stream@1``: the order the PDF content
stream emits, which is what PyMuPDF preserves when its words are read unsorted.
The library's ``sort=True`` mode was measured on a two-column fixture and found
to sort by vertical position across the whole page, interleaving the columns --
so it is *not* used, and this adapter claims no layout reconstruction. Reading
order is a declared property, not a promise about visual order (R5, FR-036).

**Rotation.** Measured, not assumed: PyMuPDF reports word boxes in *unrotated*
page space while ``page.rect`` is the *displayed* size. Normalizing one against
the other would place every box wrongly on a rotated page, so this adapter maps
each box through ``page.rotation_matrix`` first (R8, FR-006).
"""

from __future__ import annotations

from itertools import groupby
from typing import TYPE_CHECKING, Any, Final

import pymupdf

from docdoc.ingest.capabilities import ParserCapabilities
from docdoc.ingest.errors import ParserError, UnsupportedDocumentError
from docdoc.ingest.normalize import DocumentBuilder, normalize_bbox
from docdoc.ingest.options import options_fingerprint
from docdoc.ingest.source import PDF
from docdoc.kernel import BBox, DocdocError, Document, GeometryError, IngestProvenance

if TYPE_CHECKING:
    from collections.abc import Mapping

    from docdoc.ingest.options import TransportSettings
    from docdoc.ingest.source import SourceFile
    from docdoc.kernel import TextLayerRecord

__all__ = ["PdfTextParser", "page_text_lengths"]

PARSER_ID: Final = "pdf-text"
ADAPTER_VERSION: Final = "1.0.0"

#: Index positions in a PyMuPDF "words" tuple.
_X0, _Y0, _X1, _Y1, _WORD, _BLOCK, _LINE = 0, 1, 2, 3, 4, 5, 6


def _open(source: SourceFile) -> pymupdf.Document:
    """Open the PDF, translating every library failure into docdoc's model.

    A library exception must not cross this boundary (FR-025); an encrypted or
    truncated file is a typed, explicit refusal, never an empty document.
    """
    try:
        document = pymupdf.open(stream=source.data, filetype="pdf")
    except Exception as error:
        raise UnsupportedDocumentError(
            "the file could not be opened as a PDF",
            reason="corrupt",
            blob_id=source.blob_id,
            media_type=source.media_type,
            parser_id=PARSER_ID,
        ) from error

    if document.needs_pass:
        document.close()
        raise UnsupportedDocumentError(
            "the PDF is password-protected; docdoc does not guess passwords",
            reason="encrypted",
            blob_id=source.blob_id,
            media_type=source.media_type,
            parser_id=PARSER_ID,
        )
    return document


def page_text_lengths(source: SourceFile) -> tuple[str, ...]:
    """The raw text of each page, for the text-layer assessment.

    Lives here rather than in ``assess`` because reading a PDF is this module's
    job and the assessment may not import a provider library (Principle IV).
    Returns the text itself, so the counting rule -- and therefore the verdict --
    stays entirely inside the assessment, where it is versioned.
    """
    document = _open(source)
    try:
        return tuple(document[index].get_text() for index in range(document.page_count))
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
    #: Adapter version plus the library version. A PyMuPDF upgrade that changes
    #: extraction changes document identity, which is the point (ING-9).
    version: Final = f"{ADAPTER_VERSION}+pymupdf-{pymupdf.__version__}"
    capabilities: Final = ParserCapabilities(
        text=True,
        geometry=True,
        tables=False,
        handwriting=False,
        media_types=frozenset({PDF}),
        requires_network=False,
    )
    reading_order: Final = "pymupdf-stream@1"

    def parse(
        self,
        source: SourceFile,
        options: Mapping[str, Any],
        transport: TransportSettings | None = None,
        text_layer: TextLayerRecord | None = None,
    ) -> Document:
        """Read the PDF and build the Document. ``transport`` is ignored."""
        materialized, options_hash = options_fingerprint(options)
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

    def _read(self, document: pymupdf.Document, source: SourceFile) -> DocumentBuilder:
        builder = DocumentBuilder(geometry=True)

        for index in range(document.page_count):
            page = document[index]
            # page.rect is the page as *displayed*; the words below are in
            # unrotated space, which is why each box is mapped first.
            width, height = page.rect.width, page.rect.height
            builder.start_page(width=width, height=height, rotation=page.rotation)

            try:
                words = page.get_text("words")
            except Exception as error:
                raise ParserError(
                    f"page {index} could not be read",
                    reason="internal",
                    parser_id=self.id,
                    blob_id=source.blob_id,
                ) from error

            rotation = page.rotation_matrix
            for _, line in groupby(words, key=lambda word: (word[_BLOCK], word[_LINE])):
                builder.add_line(
                    [
                        (
                            word[_WORD],
                            self._box(word, rotation, width, height, index, source),
                        )
                        for word in line
                    ]
                )

        return builder

    def _box(
        self,
        word: tuple[Any, ...],
        rotation: pymupdf.Matrix,
        width: float,
        height: float,
        page_index: int,
        source: SourceFile,
    ) -> BBox:
        placed = pymupdf.Rect(word[_X0], word[_Y0], word[_X1], word[_Y1]) * rotation
        try:
            return normalize_bbox(
                placed.x0,
                placed.y0,
                placed.x1,
                placed.y1,
                width=width,
                height=height,
                page_index=page_index,
            )
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
