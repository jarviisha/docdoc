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

#: Unset by default: no cap, unlike the shim's hard-coded ``MAX_PAGES = 3``.
#: A deployment that wants one sets ``MAX_PAGES`` and gets a 413 over it --
#: never a document silently read only partway through.
_max_pages_env = os.environ.get("MAX_PAGES")
MAX_PAGES: int | None = int(_max_pages_env) if _max_pages_env else None

#: The shim's own preview width: readable, light enough that `visualize`'s
#: callers do not pay much for it.
PREVIEW_WIDTH = 1200


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

    try:
        images, is_pdf = ocr.pages(data, req.fileType)
    except Exception as error:  # any unreadable file maps to one body shape
        return _error(400, f"file could not be read: {type(error).__name__}")

    # ponytail: every page is rendered before the cap below can reject the
    # document -- simplest correct option (one render path, no second way to
    # peek a PDF's page count), ceiling is wasted rendering on a document
    # that gets refused anyway. Upgrade if a deployment with `MAX_PAGES` set
    # actually receives oversized PDFs often enough to notice: peek the page
    # count with `pdfium.PdfDocument` before calling `ocr.pages`.
    if MAX_PAGES is not None and len(images) > MAX_PAGES:
        # Rejected whole, not truncated: a 413 over the cap is the one
        # defence that lets a caller tell "read completely" from "read
        # partway" apart, which a silently short result would not (global
        # constraints: "never truncate silently").
        return _error(413, f"document has {len(images)} pages, over the {MAX_PAGES} limit")

    try:
        results = []
        for index, image in enumerate(images):
            tsv = ocr.run_tesseract(_png_bytes(image), TESSERACT_LANGS)
            lines = ocr.lines_from_tsv(tsv)
            pruned = {
                "rec_texts": [text for text, _, _ in lines],
                "rec_boxes": [box for _, box, _ in lines],
                "rec_scores": [score for _, _, score in lines],
            }
            page: dict[str, Any] = {"prunedResult": pruned}
            if req.visualize and index == 0:
                page["inputImage"] = _preview(image)
            results.append(page)
    except Exception as error:  # subprocess failure or timeout, one body shape
        return _error(500, f"tesseract failed: {type(error).__name__}")

    sizes = [{"width": image.width, "height": image.height} for image in images]
    if is_pdf:
        # `numPages` names the whole document's page count -- every page is
        # always rendered (the over-cap case returns 413 above instead), so
        # this is simply `len(images)`, never a chunk's count.
        data_info = {"type": "pdf", "numPages": len(images), "pages": sizes}
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
