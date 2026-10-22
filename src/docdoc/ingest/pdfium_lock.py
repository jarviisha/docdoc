"""The one lock every PDFium call in the process holds.

PDFium is not thread-safe (pypdfium2's docs), and three callers share it: the
native `pdf-text` parser, the PaddleX adapter's PDF splitting, and the console's
page renderer, which the API runs from its threadpool. A lock per caller would
let two of them into PDFium at once, so there is one, here in ingest because
ingest may not import the API. Re-entrant, so a helper that takes it can be
called from code that already holds it.
"""

from __future__ import annotations

import threading
from typing import Final

__all__ = ["PDFIUM_LOCK"]

PDFIUM_LOCK: Final = threading.RLock()
