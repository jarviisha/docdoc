"""T016 — limits are enforced before anything is parsed or transmitted (ING-2).

The constitution makes file size limits, allowed MIME types, and page limits MVP
security requirements. The property that matters is not only that an over-limit
file is rejected, but that it is rejected *early* -- rejecting a 900 MB file
after uploading it to a paid service is not a limit, it is a bill.
"""

from __future__ import annotations

import pytest

from docdoc.ingest.errors import UnsupportedDocumentError
from docdoc.ingest.source import Limits, SourceFile

PDF_BYTES = b"%PDF-1.7\n" + b"x" * 200


class TestSizeLimit:
    def test_over_size_is_rejected(self) -> None:
        limits = Limits(max_size_bytes=64)

        with pytest.raises(UnsupportedDocumentError) as caught:
            SourceFile.from_bytes(PDF_BYTES, limits=limits)

        assert caught.value.reason == "size_limit"
        assert caught.value.blob_id is not None

    def test_exactly_at_the_limit_is_accepted(self) -> None:
        limits = Limits(max_size_bytes=len(PDF_BYTES))

        assert SourceFile.from_bytes(PDF_BYTES, limits=limits).size_bytes == len(PDF_BYTES)

    def test_rejection_happens_at_construction_not_at_parse(self) -> None:
        # There is no object to parse with: construction is the gate.
        limits = Limits(max_size_bytes=8)

        with pytest.raises(UnsupportedDocumentError):
            SourceFile.from_bytes(PDF_BYTES, limits=limits)


class TestPageLimit:
    def test_over_page_count_is_rejected(self) -> None:
        source = SourceFile.from_bytes(PDF_BYTES)
        limits = Limits(max_pages=10)

        with pytest.raises(UnsupportedDocumentError) as caught:
            source.check_page_count(11, limits)

        assert caught.value.reason == "page_limit"

    def test_exactly_at_the_limit_is_accepted(self) -> None:
        source = SourceFile.from_bytes(PDF_BYTES)

        source.check_page_count(10, Limits(max_pages=10))


class TestErrorsCarryNoContent:
    def test_message_and_attributes_never_include_the_bytes(self) -> None:
        secret = b"%PDF-1.7\nACCOUNT 12345678 SORT 99-99-99\n" + b"y" * 300
        limits = Limits(max_size_bytes=32)

        with pytest.raises(UnsupportedDocumentError) as caught:
            SourceFile.from_bytes(secret, limits=limits)

        rendered = f"{caught.value} {caught.value.__dict__}"
        assert "ACCOUNT" not in rendered
        assert "12345678" not in rendered
