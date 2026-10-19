"""A Tesseract server that answers the PaddleX serving CLI's own ``/ocr``
wire protocol (see ``docdoc.ingest.parsers.paddlex``), so mmerp -- which
calls the hand-written shim this replaces directly, by URL, with no docdoc
in between -- keeps working unchanged.

Deliberately has **no docdoc import** (task brief; global constraints: this
whole directory is checked for that). This is a separate deployable, built
and run on its own; the only contract it owes docdoc is the wire shape
``docdoc.ingest.parsers.paddlex.pages_of``/``build_document`` already read.

Ported from ``.superpowers/sdd/2026-10-19-ocr-gateway/shim-app.py``, keeping
its column-split OCR logic and page rendering (now in ``ocr.py``) and its
``visualize`` preview (mmerp still sends it), dropping its defects:
``MAX_PAGES=3`` silently truncating a long document, a success-shaped 200
whose body says ``errorCode: 500``, no ``rec_scores``, and a hard-coded
``vie``.
"""

from __future__ import annotations

import base64
import binascii
import os
import uuid
from io import BytesIO
from typing import TYPE_CHECKING, Any

import ocr
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

if TYPE_CHECKING:
    from PIL import Image

app = FastAPI()

#: ``vie+eng`` by default -- both languages the shim's host ever served,
#: instead of its hard-coded ``-l vie`` alone.
TESSERACT_LANGS = os.environ.get("TESSERACT_LANGS", "vie+eng")


def _parse_max_pages(raw: str | None) -> int | None:
    """Unset means no cap (`None`), unlike the shim's hard-coded
    ``MAX_PAGES = 3``. Anything else must be a positive integer -- a typo'd
    ``MAX_PAGES=0`` (or a negative number) would otherwise silently reject
    *every* request forever, which is a deployment mistake that deserves to
    fail loudly at startup, not be discovered one 413 at a time in
    production.
    """
    if not raw:
        return None
    try:
        parsed = int(raw)
    except ValueError:
        raise RuntimeError(f"MAX_PAGES={raw!r} is not an integer") from None
    if parsed < 1:
        raise RuntimeError(
            f"MAX_PAGES={raw!r} must be a positive integer; a value below 1 would refuse "
            "every request, which is never what setting this cap is meant to do"
        )
    return parsed


#: A deployment that wants a cap sets `MAX_PAGES` and gets a 413 over it --
#: never a document silently read only partway through.
MAX_PAGES: int | None = _parse_max_pages(os.environ.get("MAX_PAGES"))

#: The shim's own preview width: readable, light enough that `visualize`'s
#: callers do not pay much for it.
PREVIEW_WIDTH = 1200

#: Modes Pillow's PNG writer accepts directly. CMYK (and a handful of other
#: modes a scanner or a converted image can carry) raise `OSError` from
#: `Image.save(..., "PNG")` -- a valid, readable input failing with a 500
#: that has nothing to do with tesseract. Converted to RGB instead of left
#: to crash.
_PNG_SAFE_MODES = frozenset({"1", "L", "LA", "I", "P", "RGB", "RGBA"})


class OcrRequest(BaseModel):
    file: str
    fileType: int | None = None  # noqa: N815 -- the wire field name, PaddleX's own casing
    visualize: bool | None = None


def _error(status: int, message: str) -> JSONResponse:
    """The PaddleX error body shape, at the matching HTTP status -- never a
    200 carrying ``errorCode`` inside it, the shim's own failure mode that
    docdoc's ``PaddleXParser`` has to specially detect (Review Focus 4).
    """
    return JSONResponse(
        status_code=status,
        content={"logId": str(uuid.uuid4()), "errorCode": status, "errorMsg": message},
    )


def _png_bytes(image: Image.Image) -> bytes:
    if image.mode not in _PNG_SAFE_MODES:
        image = image.convert("RGB")
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return buffer.getvalue()


def _preview(image: Image.Image) -> str:
    """A small JPEG, base64-encoded -- ``visualize``'s ``inputImage``,
    exactly as the shim renders it: mmerp calls this server directly and
    already depends on this shape.
    """
    image = image.convert("RGB")
    if image.width > PREVIEW_WIDTH:
        image = image.resize((PREVIEW_WIDTH, round(image.height * PREVIEW_WIDTH / image.width)))
    buffer = BytesIO()
    image.save(buffer, "JPEG", quality=80)
    return base64.b64encode(buffer.getvalue()).decode()


@app.get("/health")
def health() -> dict[str, Any]:
    return {"errorCode": 0, "errorMsg": "Healthy"}


@app.post("/ocr")
def run_ocr(req: OcrRequest) -> JSONResponse:
    try:
        data = base64.b64decode(req.file, validate=True)
    except binascii.Error:
        return _error(400, "file is not valid base64")

    if ocr.is_pdf(data, req.fileType):
        try:
            page_count = ocr.pdf_page_count(data)
        except Exception as error:  # any unreadable file maps to one body shape
            return _error(400, f"file could not be read: {type(error).__name__}")
        if MAX_PAGES is not None and page_count > MAX_PAGES:
            # Refused before a single page is rendered, let alone OCR'd --
            # `pdf_page_count` above is the only pdfium work this document
            # costs. A silently short result is never acceptable (global
            # constraints: "never truncate silently"), so this is a whole
            # rejection instead.
            return _error(413, f"document has {page_count} pages, over the {MAX_PAGES} limit")
    else:
        page_count = 1  # An image is always exactly one page.

    pages_iter, is_pdf_file = ocr.iter_pages(data, req.fileType)

    results: list[dict[str, Any]] = []
    sizes: list[dict[str, int]] = []
    index = 0
    while True:
        try:
            image = next(pages_iter)
        except StopIteration:
            break
        except ocr.UnsupportedImageError as error:
            return _error(400, str(error))
        except Exception as error:  # a render/decode failure, not a tesseract one
            return _error(400, f"file could not be read: {type(error).__name__}")

        sizes.append({"width": image.width, "height": image.height})
        try:
            tsv = ocr.run_tesseract(_png_bytes(image), TESSERACT_LANGS)
            lines = ocr.lines_from_tsv(tsv)
        except Exception as error:  # subprocess failure or timeout, one body shape
            return _error(500, f"tesseract failed: {type(error).__name__}")

        pruned = {
            "rec_texts": [text for text, _, _ in lines],
            "rec_boxes": [box for _, box, _ in lines],
            "rec_scores": [score for _, _, score in lines],
        }
        page: dict[str, Any] = {"prunedResult": pruned}
        if req.visualize and index == 0:
            page["inputImage"] = _preview(image)
        results.append(page)
        index += 1

    if is_pdf_file:
        # `numPages` names the whole document's page count, learned up
        # front from `pdf_page_count` -- never a chunk's count, and the same
        # number regardless of how this loop happened to iterate.
        data_info = {"type": "pdf", "numPages": page_count, "pages": sizes}
    else:
        data_info = {"type": "image", **sizes[0]}

    return JSONResponse(
        {
            "logId": str(uuid.uuid4()),
            "errorCode": 0,
            "errorMsg": "Success",
            "result": {"ocrResults": results, "dataInfo": data_info},
        }
    )
