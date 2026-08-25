"""T033 — the native path end to end, and the identity rules around it.

Covers SC-005 (repeat runs agree), SC-006 (identity changes when and only when
its inputs do), and SC-018 (transport settings are not among those inputs). The
converse cases matter as much as the forward ones: a determinism test that only
checks "same input, same id" would pass even if identity were a constant.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from docdoc.ingest import parse
from docdoc.ingest.errors import ParserCapabilityError, UnsupportedDocumentError
from docdoc.ingest.options import TransportSettings
from docdoc.ingest.source import Limits, SourceFile

# SC-013: the offline suite must pass on a base install — one with no provider
# SDK at all. Constitution XII: "Provider adapters MUST have integration tests;
# those tests MUST NOT be required to run the unit and property suites." These
# tests exercise the native PDF path, so they skip rather than fail when it is absent.
pytest.importorskip("pymupdf")

FIXTURES = Path(__file__).parent.parent / "fixtures"
INVOICE = FIXTURES / "pdf" / "digital_invoice.pdf"


def invoice_bytes() -> bytes:
    return INVOICE.read_bytes()


class TestEndToEnd:
    def test_bytes_in_document_out(self) -> None:
        document = parse(invoice_bytes())

        assert "INV-001" in document.text
        assert len(document.pages) == 1

    def test_a_value_resolves_to_a_page_and_a_box(self) -> None:
        document = parse(invoice_bytes())

        (span,) = document.find("INV-001")
        (geometry,) = document.locate(span)

        assert geometry.page_index == 0
        assert all(0.0 <= value <= 1.0 for value in geometry.bbox)

    def test_a_prepared_source_file_is_accepted_too(self) -> None:
        source = SourceFile.from_bytes(invoice_bytes(), filename="invoice.pdf")

        assert parse(source).source.filename == "invoice.pdf"

    def test_provenance_names_the_parser(self) -> None:
        document = parse(invoice_bytes())

        assert document.provenance.parser_id == "pdf-text"
        assert "pymupdf" in document.provenance.parser_version
        assert document.provenance.reading_order == "pymupdf-stream@1"

    def test_the_document_references_the_bytes_without_carrying_them(self) -> None:
        document = parse(invoice_bytes())

        assert document.source.blob_id.startswith("sha256:")
        assert document.source.size_bytes == len(invoice_bytes())

    def test_a_blank_page_is_present_with_zero_tokens(self) -> None:
        document = parse((FIXTURES / "pdf" / "mixed_pages.pdf").read_bytes())
        empty = [page for page in document.pages if page.span.start == page.span.end]

        assert len(empty) == 1


class TestIdentity:
    def test_the_same_bytes_produce_the_same_document(self) -> None:
        first = parse(invoice_bytes())
        second = parse(invoice_bytes())

        assert first.id == second.id
        assert first.text == second.text
        assert list(first.tokens) == list(second.tokens)

    def test_different_options_produce_a_different_identity(self) -> None:
        plain = parse(invoice_bytes())
        with_options = parse(invoice_bytes(), options={"mode": "strict"})

        assert plain.id != with_options.id

    def test_option_key_order_does_not_matter(self) -> None:
        one = parse(invoice_bytes(), options={"a": 1, "b": 2})
        other = parse(invoice_bytes(), options={"b": 2, "a": 1})

        assert one.id == other.id

    def test_a_different_parser_version_produces_a_different_identity(self) -> None:
        # SC-006's converse. Simulated by asking the kernel directly, because
        # the whole point is that a *future* library upgrade must not collide
        # with today's results.
        from docdoc.kernel import document_id_for, options_hash_for

        document = parse(invoice_bytes())
        bumped = document_id_for(
            blob_id=document.source.blob_id,
            parser_id="pdf-text",
            parser_version="1.0.0+pymupdf-99.0.0",
            options_hash=options_hash_for({}),
        )

        assert document.id != bumped

    def test_transport_settings_do_not_touch_identity(self) -> None:
        default = parse(invoice_bytes())
        impatient = parse(
            invoice_bytes(),
            transport=TransportSettings(max_attempts=1, deadline_s=1.0, attempt_timeout_s=1.0),
        )

        assert default.id == impatient.id


class TestFailuresAreExplicit:
    def test_an_unrecognized_file_is_refused(self) -> None:
        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(b"this is not a document")

        assert caught.value.reason == "mime_type"

    def test_an_over_size_file_is_refused_before_parsing(self) -> None:
        with pytest.raises(UnsupportedDocumentError) as caught:
            parse(invoice_bytes(), limits=Limits(max_size_bytes=100))

        assert caught.value.reason == "size_limit"

    def test_an_encrypted_file_is_refused(self) -> None:
        with pytest.raises(UnsupportedDocumentError) as caught:
            parse((FIXTURES / "pdf" / "encrypted.pdf").read_bytes())

        assert caught.value.reason == "encrypted"

    def test_an_image_has_no_native_parser_and_says_so(self) -> None:
        # No recognition path yet, so this is the honest answer rather than a
        # silent empty document.
        with pytest.raises(ParserCapabilityError):
            parse((FIXTURES / "image" / "sample_page.png").read_bytes())

    def test_no_failure_returns_a_partial_document(self) -> None:
        for payload in (b"", b"junk", b"%PDF-1.7\nbroken"):
            with pytest.raises(Exception) as caught:  # noqa: PT011
                parse(payload)
            from docdoc.kernel import DocdocError

            assert isinstance(caught.value, DocdocError)


class TestObservability:
    def test_a_success_emits_exactly_one_event(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="docdoc.ingest")

        document = parse(invoice_bytes())

        (record,) = caplog.records
        fields = record.docdoc  # type: ignore[attr-defined]
        assert fields["outcome"] == "ok"
        assert fields["document_id"] == document.id
        assert fields["pages"] == 1

    def test_a_failure_emits_exactly_one_event(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.INFO, logger="docdoc.ingest")

        with pytest.raises(UnsupportedDocumentError):
            parse((FIXTURES / "pdf" / "encrypted.pdf").read_bytes())

        (record,) = caplog.records
        fields = record.docdoc  # type: ignore[attr-defined]
        assert fields["outcome"] == "error"
        assert fields["error_reason"] == "encrypted"

    def test_no_document_content_reaches_the_log(self, caplog: pytest.LogCaptureFixture) -> None:
        caplog.set_level(logging.DEBUG, logger="docdoc.ingest")

        parse(invoice_bytes())

        rendered = " ".join(
            f"{record.getMessage()} {getattr(record, 'docdoc', '')}" for record in caplog.records
        )
        for secret in ("INV-001", "ACME", "228.00", "Northwind"):
            assert secret not in rendered


class TestOffline:
    def test_the_whole_path_runs_with_the_network_unavailable(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """SC-001 — no credentials, no network, no infrastructure.

        Enforced by breaking sockets outright rather than by trusting that no
        call is made.
        """
        import socket

        def refuse(*args: object, **kwargs: object) -> None:
            raise AssertionError("the native path must not touch the network")

        monkeypatch.setattr(socket, "socket", refuse)
        monkeypatch.setattr(socket, "create_connection", refuse)

        document = parse(invoice_bytes())

        assert document.find("INV-001")
