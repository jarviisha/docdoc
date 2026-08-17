"""T015 — the bytes decide the type, not the caller (ING-1, research.md R9)."""

from __future__ import annotations

import pytest

from docdoc.ingest.errors import UnsupportedDocumentError
from docdoc.ingest.source import Limits, SourceFile, detect_media_type

PDF_BYTES = b"%PDF-1.7\n% fake but correctly signed\n"
PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG_BYTES = b"\xff\xd8\xff\xe0" + b"\x00" * 32
TIFF_BYTES = b"II*\x00" + b"\x00" * 32


class TestDetection:
    @pytest.mark.parametrize(
        ("data", "expected"),
        [
            (PDF_BYTES, "application/pdf"),
            (PNG_BYTES, "image/png"),
            (JPEG_BYTES, "image/jpeg"),
            (TIFF_BYTES, "image/tiff"),
        ],
    )
    def test_signatures(self, data: bytes, expected: str) -> None:
        assert detect_media_type(data) == expected

    def test_unrecognized_signature_is_none(self) -> None:
        assert detect_media_type(b"not a document at all") is None

    def test_empty_file(self) -> None:
        assert detect_media_type(b"") is None


class TestDeclaredTypeIsNotTrusted:
    def test_png_named_as_a_pdf_is_a_png(self) -> None:
        source = SourceFile.from_bytes(
            PNG_BYTES, declared_media_type="application/pdf", filename="invoice.pdf"
        )

        assert source.media_type == "image/png"
        assert source.declared_media_type == "application/pdf"

    def test_a_lie_about_the_type_does_not_make_a_file_supported(self) -> None:
        # Claiming PDF does not smuggle an unrecognized file past the gate.
        with pytest.raises(UnsupportedDocumentError) as caught:
            SourceFile.from_bytes(b"junk", declared_media_type="application/pdf")

        assert caught.value.reason == "mime_type"

    def test_tiff_is_detected_so_it_can_be_rejected_by_name(self) -> None:
        # The useful error is "TIFF is not accepted", not "unrecognizable file".
        with pytest.raises(UnsupportedDocumentError) as caught:
            SourceFile.from_bytes(TIFF_BYTES)

        assert caught.value.reason == "mime_type"
        assert caught.value.media_type == "image/tiff"
        assert "image/tiff" in str(caught.value)


class TestIdentityAndReference:
    def test_blob_id_depends_only_on_the_bytes(self) -> None:
        a = SourceFile.from_bytes(PDF_BYTES, filename="a.pdf", declared_media_type="x/y")
        b = SourceFile.from_bytes(PDF_BYTES, filename="totally-different.pdf")

        assert a.blob_id == b.blob_id

    def test_different_bytes_differ(self) -> None:
        a = SourceFile.from_bytes(PDF_BYTES)
        b = SourceFile.from_bytes(PDF_BYTES + b"more")

        assert a.blob_id != b.blob_id

    def test_blob_ref_carries_identity_not_bytes(self) -> None:
        source = SourceFile.from_bytes(PDF_BYTES, filename="invoice.pdf")
        ref = source.blob_ref()

        assert ref.blob_id == source.blob_id
        assert ref.size_bytes == len(PDF_BYTES)
        assert not hasattr(ref, "data")

    def test_source_is_immutable(self) -> None:
        source = SourceFile.from_bytes(PDF_BYTES)

        with pytest.raises(Exception, match=r"frozen|immutable"):
            source.media_type = "image/png"  # type: ignore[misc]


class TestAcceptedTypes:
    def test_tiff_can_be_opted_into_by_a_deployment(self) -> None:
        limits = Limits(allowed_media_types=frozenset({"image/tiff"}))
        source = SourceFile.from_bytes(TIFF_BYTES, limits=limits)

        assert source.media_type == "image/tiff"
