"""PDFium is not thread-safe, so every caller in the process shares one lock."""

from __future__ import annotations

import threading

import pytest

pytest.importorskip("fastapi")

from docdoc.api import pages
from docdoc.ingest.pdfium_lock import PDFIUM_LOCK


def test_the_lock_is_reentrant() -> None:
    assert isinstance(PDFIUM_LOCK, type(threading.RLock()))


def test_the_page_renderer_shares_the_ingest_lock() -> None:
    assert pages._PDFIUM is PDFIUM_LOCK
