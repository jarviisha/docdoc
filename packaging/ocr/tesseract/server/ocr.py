"""Tesseract, speaking only in TSV and pixels -- no FastAPI, no PaddleX
shapes, and (task brief, global constraints) no docdoc import. `app.py` is
the only place either of those lives; this module renders pages and reads
what tesseract says about them, the same two jobs the shim being replaced
did (see .superpowers/sdd/2026-10-19-ocr-gateway/shim-app.py), minus its
`MAX_PAGES` truncation and its hard-coded ``-l vie``.
"""

from __future__ import annotations

import csv
import io
import subprocess
import tempfile
import threading
from typing import TYPE_CHECKING

import pypdfium2 as pdfium
from PIL import Image

if TYPE_CHECKING:
    from collections.abc import Iterator

#: 300 dpi, long side capped at `MAX_SIDE` -- the shim's own choice. A4 at
#: 300 dpi is already 3508px on its long side, so the cap only bites on a
#: page that is unusually large, not on an ordinary one.
_DPI_SCALE = 300 / 72
MAX_SIDE = 3500

#: The shim's own timeout: long enough for a dense scanned page, short
#: enough that one stuck request does not hang a worker forever.
TESSERACT_TIMEOUT_S = 120

#: pypdfium2 is explicitly documented as not thread-safe -- not just two
#: calls touching the *same* document, but any two calls into the library
#: from different threads at once, because PDFium keeps process-global state
#: under the hood. `app.py`'s `POST /ocr` is a plain (sync) `def`, which
#: FastAPI/Starlette runs in a worker-thread pool -- so two concurrent
#: requests really can call into pdfium from two threads at the same moment,
#: and the documented failure mode is a segfault, not an exception either
#: side could catch. Every call into `pdfium` in this module -- open, page
#: count, get a page's size, render it, close -- goes through this lock, one
#: call at a time, process-wide.
_PDFIUM_LOCK = threading.Lock()


class UnsupportedImageError(ValueError):
    """A file this server deliberately refuses to OCR rather than silently
    reading only part of it. Right now that is exactly one case: a
    multi-frame image (an animated or multi-page TIFF) -- see `_load_image`.
    """


def run_tesseract(png: bytes, langs: str) -> str:
    """Run tesseract over one rendered page, returning its TSV output.

    A temp file, not stdin: tesseract's CLI reads an image *path*, which is
    what the shim already worked around this way.
    """
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        handle.write(png)
        handle.flush()
        completed = subprocess.run(
            ["tesseract", handle.name, "-", "-l", langs, "tsv"],
            capture_output=True,
            check=True,
            timeout=TESSERACT_TIMEOUT_S,
        )
    return completed.stdout.decode()


def lines_from_tsv(tsv: str) -> list[tuple[str, list[int], float | None]]:
    """One ``(text, [x1, y1, x2, y2], score)`` per recognized line.

    The column split is the shim's own ``read()``, ported verbatim:
    tesseract's line grouping (block/par/line) sometimes puts two genuinely
    separate columns on one "line" -- a letterhead's agency name beside its
    national motto was the shim's own example -- so a gap wider than 3x the
    line's tallest word starts a new line here rather than trusting
    tesseract's own grouping alone.

    ``score`` is new; the shim never reported one. It is the mean of each
    word's ``conf`` (ignoring tesseract's "no confidence" sentinel, ``-1``)
    divided by 100, landing in the same 0..1 range PaddleX's own
    ``rec_scores`` uses -- or ``None`` when no word in the line carries a
    confidence at all, "not reported" rather than zero.
    """
    rows: dict[tuple[int, int, int], list[dict[str, str]]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] == "5" and row["text"].strip():
            key = (int(row["block_num"]), int(row["par_num"]), int(row["line_num"]))
            rows.setdefault(key, []).append(row)

    out: list[tuple[str, list[int], float | None]] = []

    def flush(words: list[dict[str, str]]) -> None:
        x1 = min(int(w["left"]) for w in words)
        y1 = min(int(w["top"]) for w in words)
        x2 = max(int(w["left"]) + int(w["width"]) for w in words)
        y2 = max(int(w["top"]) + int(w["height"]) for w in words)
        text = " ".join(w["text"] for w in words)
        confs = [float(w["conf"]) for w in words if float(w["conf"]) != -1]
        score = (sum(confs) / len(confs) / 100) if confs else None
        out.append((text, [x1, y1, x2, y2], score))

    for words in rows.values():
        current: list[dict[str, str]] = []
        previous_right: int | None = None
        # Tallest word, not the line's average: a diacritic or a hyphen sits
        # lower than the rest of the line, and sizing the column gap on it
        # would cut a real word in two (the shim's own comment, on the
        # Vietnamese "Doc lap - Tu do" national motto).
        tallest = max(int(w["height"]) for w in words)
        for word in words:
            left = int(word["left"])
            if previous_right is not None and left - previous_right > 3 * tallest and current:
                flush(current)
                current = []
            current.append(word)
            previous_right = left + int(word["width"])
        flush(current)
    return out


def is_pdf(data: bytes, file_type: int | None) -> bool:
    """Whether ``data`` is a PDF, by the same test the shim used: a declared
    ``fileType`` of 0, or the bytes' own signature. Shared between the
    page-count check and the actual render so the two never disagree.
    """
    return file_type == 0 or data[:5] == b"%PDF-"


def pdf_page_count(data: bytes) -> int:
    """Just the page count: opens the document, reads its length, closes it
    -- no page is rendered. This is what lets `app.py` refuse a document
    over ``MAX_PAGES`` with a 413 *before* doing any rendering work on it,
    rather than rendering everything and only then deciding to reject it.

    Raises whatever `pdfium.PdfDocument` raises on a corrupt or encrypted
    file; `app.py` maps that to 400, the same as any other unreadable file.
    """
    with _PDFIUM_LOCK:
        document = pdfium.PdfDocument(data)
        try:
            return len(document)
        finally:
            document.close()


def iter_pages(data: bytes, file_type: int | None) -> tuple[Iterator[Image.Image], bool]:
    """Every page of ``data``, one at a time, plus whether it was a PDF.

    A generator, not a list: a caller OCRs and discards each page before
    asking for the next one, so peak memory is one rendered page (roughly
    26MB for a 3500x3500 RGB image), never the whole document at once. No
    page is skipped and no cap is applied here -- enforcing one without
    truncating is `app.py`'s job, using `pdf_page_count` above, before this
    is ever called.
    """
    if is_pdf(data, file_type):
        return _iter_pdf_pages(data), True
    return _iter_single_image(data), False


def _iter_single_image(data: bytes) -> Iterator[Image.Image]:
    yield _load_image(data)


def _iter_pdf_pages(data: bytes) -> Iterator[Image.Image]:
    with _PDFIUM_LOCK:
        document = pdfium.PdfDocument(data)
        count = len(document)
    try:
        for index in range(count):
            with _PDFIUM_LOCK:
                width, height = document[index].get_size()
                scale = min(_DPI_SCALE, MAX_SIDE / max(width, height))
                image = document[index].render(scale=scale).to_pil()
            # Released between pages on purpose: OCR-ing the page just
            # rendered (the slow step, a tesseract subprocess) does not
            # touch pdfium and so must not hold this lock while it runs --
            # only the render call above needs to be serialized.
            yield image
    finally:
        with _PDFIUM_LOCK:
            document.close()


def _load_image(data: bytes) -> Image.Image:
    image = Image.open(io.BytesIO(data))
    # Forced now rather than left lazy: a truncated or malformed image can
    # open successfully and only fail once its pixels are decoded, which
    # would otherwise surface later as a 500 (a tesseract failure) rather
    # than the 400 (an unreadable file) it actually is.
    image.load()

    # A multi-frame image (an animated or multi-page TIFF) has no page
    # count this protocol can report truthfully from one `prunedResult`: OCR
    # would read frame 0 alone and the response would claim one page while
    # silently discarding the rest. Refused outright instead (ING-7's
    # "no silent partial result", applied here even though this path never
    # reaches docdoc's own check for it).
    n_frames = getattr(image, "n_frames", 1)
    if n_frames > 1:
        raise UnsupportedImageError(
            f"image has {n_frames} frames; only the first would be read, and this "
            "server refuses that rather than silently returning a partial result"
        )
    return image
