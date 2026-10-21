"""Page images for an OCR run's source document (019).

The console draws a run's token boxes over the page they came from. PNG and
JPEG are already images and are served as stored; a PDF page is rendered with
`pypdfium2` (the `docdoc[ocr]` extra) and encoded by `encode_png`, a stdlib
encoder, so serving a page adds no dependency.
"""

from __future__ import annotations

import struct
import threading
import zlib

from docdoc.ingest.source import JPEG, PDF, PNG, detect_media_type

#: The longer edge of a rendered PDF page, in pixels.
MAX_EDGE = 2000
#: Never render a small page above this resolution.
_MAX_DPI = 200

#: PDFium is not thread-safe (pypdfium2's docs): every call into it holds this.
_PDFIUM = threading.Lock()

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_COLOR_TYPE = {3: 2, 4: 6}  # channels -> PNG colour type: truecolour, truecolour + alpha


def _chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def encode_png(
    width: int, height: int, stride: int, channels: int, buffer: bytes, *, bgr: bool
) -> bytes:
    """An 8-bit RGB (3 channels) or RGBA (4) PNG of a row-major pixel buffer.

    Each row is `stride` bytes, of which the first `width * channels` are
    pixels; the rest is padding and is dropped. With `bgr`, the buffer is in
    BGR(A) order -- pdfium's -- and the first and third channels are swapped.
    Every row uses filter type 0, which is all a page image needs.
    """
    row = width * channels
    pixels = bytearray().join(buffer[y * stride : y * stride + row] for y in range(height))
    if bgr:
        pixels[0::channels], pixels[2::channels] = pixels[2::channels], pixels[0::channels]
    raw = b"".join(b"\x00" + pixels[y * row : (y + 1) * row] for y in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, _COLOR_TYPE[channels], 0, 0, 0)
    return (
        _PNG_SIGNATURE
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(raw, 6))
        + _chunk(b"IEND", b"")
    )


def render_pdf_page(data: bytes, page: int, *, max_edge: int = MAX_EDGE) -> bytes:
    """Page `page` (0-based) of a PDF as a PNG whose longer edge is at most `max_edge`.

    The scale is `min(max_edge / longer edge in points, 200 / 72)`. Raises
    `IndexError` for a page out of range, `ValueError` for a PDF pdfium cannot
    open, and `RuntimeError("pypdfium2")` when the `docdoc[ocr]` extra is not
    installed.
    """
    try:
        import pypdfium2
    except ImportError as exc:
        raise RuntimeError("pypdfium2") from exc

    # PDFium is not thread-safe, and the route renders from the threadpool.
    # ponytail: global pdfium lock -- serialises page renders; a render
    # subprocess if throughput matters.
    with _PDFIUM:
        try:
            document = pypdfium2.PdfDocument(data)
        except pypdfium2.PdfiumError as exc:
            # A `RuntimeError` subclass; not the missing extra, just a bad file.
            raise ValueError("unreadable PDF") from exc
        try:
            if not 0 <= page < len(document):
                raise IndexError(page)
            pdf_page = document[page]
            try:
                width, height = pdf_page.get_size()
                # pdfium ceils `size * scale`, and `max_edge / edge * edge` can land
                # a hair above `max_edge`: shaved so the ceiling is never one past it.
                scale = min(max_edge / max(width, height), _MAX_DPI / 72) * (1 - 1e-9)
                bitmap = pdf_page.render(scale=scale)
                try:
                    if bitmap.mode not in ("RGB", "RGBA", "BGR", "BGRA"):
                        raise RuntimeError(f"unexpected bitmap mode {bitmap.mode!r}")
                    shape = (bitmap.width, bitmap.height, bitmap.stride, bitmap.n_channels)
                    buffer = bytes(bitmap.buffer)
                    bgr = bitmap.mode.startswith("BGR")
                finally:
                    bitmap.close()
            finally:
                pdf_page.close()
        finally:
            document.close()
    # Encoding touches no pdfium state, so it runs outside the lock.
    return encode_png(*shape, buffer, bgr=bgr)


def page_image(data: bytes, page: int) -> tuple[bytes, str]:
    """`(image bytes, media type)` for page `page` of a stored source document.

    PNG and JPEG are one page and come back as stored; a PDF page is rendered
    to PNG. Raises `IndexError` for a page out of range and `ValueError` for any
    other media type.
    """
    media_type = detect_media_type(data)
    if media_type in (PNG, JPEG):
        if page != 0:
            raise IndexError(page)
        return data, media_type
    if media_type == PDF:
        return render_pdf_page(data, page), PNG
    raise ValueError(f"no page images for {media_type or 'an unrecognized type'}")
