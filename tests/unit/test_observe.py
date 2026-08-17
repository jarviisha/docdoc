"""T024 — the ingest.parse event, and the content it must never carry.

Two things under test. The event schema, so a downstream consumer can rely on
the fields being there (FR-040). And the absence of document content, which is
the constitution's flat prohibition and the easiest rule in a document pipeline
to break by accident (FR-029, SC-013).
"""

from __future__ import annotations

import logging

import pytest

from docdoc.ingest.observe import EVENT_FIELDS, log_parse

BLOB = "sha256:" + "a" * 64
DOCUMENT = "sha256:" + "b" * 64

# Strings that would only ever appear in a log line if document content leaked.
SECRETS = ("ACME SUPPLIES", "INV-001", "228.00", "azure-secret-key")


@pytest.fixture
def captured(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger="docdoc.ingest")
    return caplog


def fields_of(record: logging.LogRecord) -> dict[str, object]:
    payload = getattr(record, "docdoc", None)
    assert isinstance(payload, dict)
    return payload


class TestSchema:
    def test_success_event_carries_every_field(self, captured: pytest.LogCaptureFixture) -> None:
        log_parse(
            blob_id=BLOB,
            media_type="application/pdf",
            outcome="ok",
            duration_ms=840,
            document_id=DOCUMENT,
            parser_id="pdf-text",
            parser_version="1.0.0+pymupdf-1.28.2",
            text_layer_usable=True,
            text_layer_rule="text-layer@1",
            pages=12,
        )

        (record,) = captured.records
        assert set(fields_of(record)) == set(EVENT_FIELDS)

    def test_failure_event_carries_every_field_too(
        self, captured: pytest.LogCaptureFixture
    ) -> None:
        log_parse(
            blob_id=BLOB,
            media_type="application/pdf",
            outcome="error",
            duration_ms=31_000,
            parser_id="azure-di",
            attempts=3,
            error_type="ProviderError",
            error_reason="rate_limit",
        )

        (record,) = captured.records
        fields = fields_of(record)
        assert set(fields) == set(EVENT_FIELDS)
        assert fields["document_id"] is None
        assert fields["attempts"] == 3

    def test_one_event_per_parse(self, captured: pytest.LogCaptureFixture) -> None:
        for _ in range(3):
            log_parse(blob_id=BLOB, media_type="application/pdf", outcome="ok", duration_ms=1)

        assert len(captured.records) == 3

    def test_failure_is_louder_than_success(self, captured: pytest.LogCaptureFixture) -> None:
        log_parse(blob_id=BLOB, media_type="application/pdf", outcome="ok", duration_ms=1)
        log_parse(
            blob_id=BLOB,
            media_type="application/pdf",
            outcome="error",
            duration_ms=1,
            error_type="ParserError",
        )

        assert [record.levelno for record in captured.records] == [
            logging.INFO,
            logging.WARNING,
        ]


class TestNoContentLeaks:
    def test_the_event_carries_no_document_content(
        self, captured: pytest.LogCaptureFixture
    ) -> None:
        # Nothing in the signature accepts text, so this asserts the shape of
        # the API as much as the output of one call.
        log_parse(
            blob_id=BLOB,
            media_type="application/pdf",
            outcome="ok",
            duration_ms=5,
            document_id=DOCUMENT,
            parser_id="pdf-text",
            pages=1,
        )

        (record,) = captured.records
        rendered = f"{record.getMessage()} {fields_of(record)}"
        for secret in SECRETS:
            assert secret not in rendered

    def test_every_value_is_an_identifier_a_number_or_a_flag(
        self, captured: pytest.LogCaptureFixture
    ) -> None:
        log_parse(
            blob_id=BLOB,
            media_type="application/pdf",
            outcome="ok",
            duration_ms=5,
            document_id=DOCUMENT,
            parser_id="pdf-text",
            parser_version="1.0.0",
            text_layer_usable=True,
            text_layer_rule="text-layer@1",
            pages=1,
        )

        (record,) = captured.records
        for name, value in fields_of(record).items():
            assert value is None or isinstance(value, (str, int, bool)), name
            if isinstance(value, str):
                # Identifiers and hashes are short. A leaked page of text is not.
                assert len(value) <= 128, name
