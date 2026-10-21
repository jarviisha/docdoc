"""Page images for the console's OCR result view (019).

`encode_png` is a stdlib PNG encoder; dev-only Pillow decodes what it writes,
so the check is against an independent reader rather than against itself.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("PIL")

from docdoc.api.pages import encode_png, page_image, render_pdf_page

IMAGE = (Path(__file__).parent.parent / "fixtures" / "image" / "sample_page.png").read_bytes()


def _decode(png: bytes) -> Any:
    from PIL import Image

    return Image.open(io.BytesIO(png))


def _pixels(image: Any) -> list[Any]:
    width, height = image.size
    return [image.getpixel((x, y)) for y in range(height) for x in range(width)]


def _pdf(pages: int) -> bytes:
    pdfium = pytest.importorskip("pypdfium2")
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(612, 792)
    out = io.BytesIO()
    document.save(out)
    return out.getvalue()


def test_encode_png_round_trips_with_pillow() -> None:
    rgb = [(1, 2, 3), (4, 5, 6), (7, 8, 9), (10, 11, 12), (13, 14, 15), (16, 17, 18)]
    # Two bytes of padding per row: stride 11 for a 3-pixel RGB row of 9.
    rows = [bytes(sum(rgb[r * 3 : r * 3 + 3], ())) + b"\xee\xee" for r in range(2)]
    decoded = _decode(encode_png(3, 2, 11, 3, b"".join(rows), bgr=False))
    assert decoded.mode == "RGB"
    assert decoded.size == (3, 2)
    assert _pixels(decoded) == rgb

    rgba = [(1, 2, 3, 4), (5, 6, 7, 8), (9, 10, 11, 12), (13, 14, 15, 16)]
    rows = [bytes(sum(rgba[r * 2 : r * 2 + 2], ())) + b"\xee" * 4 for r in range(2)]
    decoded = _decode(encode_png(2, 2, 12, 4, b"".join(rows), bgr=False))
    assert decoded.mode == "RGBA"
    assert _pixels(decoded) == rgba


def test_encode_png_swaps_bgr() -> None:
    decoded = _decode(encode_png(1, 1, 3, 3, b"\xff\x00\x00", bgr=True))
    assert _pixels(decoded) == [(0, 0, 255)]

    decoded = _decode(encode_png(1, 1, 4, 4, b"\xff\x00\x00\x80", bgr=True))
    assert _pixels(decoded) == [(0, 0, 255, 128)]


def test_render_pdf_page_caps_the_long_edge() -> None:
    data = _pdf(2)

    decoded = _decode(render_pdf_page(data, 1))

    assert max(decoded.size) <= 2000
    assert max(decoded.size) >= 1990
    with pytest.raises(IndexError):
        render_pdf_page(data, 2)
    with pytest.raises(IndexError):
        render_pdf_page(data, -1)


def test_page_image_passes_png_through() -> None:
    assert page_image(IMAGE, 0) == (IMAGE, "image/png")
    with pytest.raises(IndexError):
        page_image(IMAGE, 1)


def test_page_image_renders_a_pdf_page() -> None:
    png, media_type = page_image(_pdf(1), 0)
    assert media_type == "image/png"
    assert png.startswith(b"\x89PNG\r\n\x1a\n")


def test_page_image_refuses_an_unknown_type() -> None:
    with pytest.raises(ValueError, match="no page images"):
        page_image(b"not an image", 0)
