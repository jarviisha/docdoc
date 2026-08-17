"""T027 — the contract every Parser must satisfy, whoever wrote it.

Parameterized over each parser that can run without credentials, so adding an
adapter means adding a line here rather than trusting that it behaves. A
third-party parser is a first-class citizen precisely because this suite exists
to hold it to the same terms (contracts/ingest-api.md §5).

Asserts ING-4 (capability honesty), ING-7 (one Document or an exception),
ING-8 (token order), and ING-9 (version embeds what can change the output).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from docdoc.ingest.options import TransportSettings
from docdoc.ingest.parser import Parser
from docdoc.ingest.source import SourceFile
from docdoc.ingest.validate import validate_output
from docdoc.kernel import Document

FIXTURES = Path(__file__).parent.parent / "fixtures"


def offline_parsers() -> list[tuple[str, Parser, Path]]:
    """Every parser that needs neither credentials nor network, with a fixture
    it accepts. A parser absent from this list is untested by this suite, which
    is itself worth noticing."""
    from docdoc.ingest.parsers.pdf_text import PdfTextParser

    return [("pdf-text", PdfTextParser(), FIXTURES / "pdf" / "digital_invoice.pdf")]


@pytest.fixture(params=offline_parsers(), ids=lambda case: case[0])
def case(request: pytest.FixtureRequest) -> tuple[Parser, SourceFile]:
    _, parser, fixture = request.param
    return parser, SourceFile.from_bytes(fixture.read_bytes(), filename=fixture.name)


def parse(parser: Parser, source: SourceFile) -> Document:
    return parser.parse(source, {}, TransportSettings())


class TestIdentityAndVersion:
    def test_satisfies_the_protocol(self, case: tuple[Parser, SourceFile]) -> None:
        parser, _ = case

        assert isinstance(parser, Parser)

    def test_id_is_provider_neutral(self, case: tuple[Parser, SourceFile]) -> None:
        """A caller must never need to know who is behind the id, so the id may
        not name a vendor (Principle IV)."""
        parser, _ = case
        vendors = ("azure", "aws", "google", "adobe", "mupdf", "pymupdf", "tesseract")

        assert parser.id
        assert not any(vendor in parser.id.lower() for vendor in vendors)

    def test_version_embeds_the_underlying_library_or_service(
        self, case: tuple[Parser, SourceFile]
    ) -> None:
        """ING-9 — a library upgrade that changes output must change identity.

        A bare adapter version would let two materially different parses collide
        in the content-addressed chain (ADR-0003).
        """
        parser, _ = case

        assert "+" in parser.version, (
            f"{parser.id} version {parser.version!r} does not name what produced it"
        )

    def test_declares_a_reading_order(self, case: tuple[Parser, SourceFile]) -> None:
        parser, _ = case

        assert parser.reading_order
        assert "@" in parser.reading_order, "a reading order must be versioned"

    def test_declares_the_media_types_it_accepts(self, case: tuple[Parser, SourceFile]) -> None:
        parser, source = case

        assert source.media_type in parser.capabilities.media_types


class TestOutput:
    def test_returns_a_valid_document(self, case: tuple[Parser, SourceFile]) -> None:
        parser, source = case
        document = parse(parser, source)

        assert isinstance(document, Document)
        assert document.source.blob_id == source.blob_id

    def test_output_survives_its_own_validation(self, case: tuple[Parser, SourceFile]) -> None:
        """ING-4 and ING-8 together: what the parser produced matches what it
        declared, and its tokens are ordered."""
        parser, source = case
        document = parse(parser, source)

        validate_output(document, parser.capabilities, parser_id=parser.id, blob_id=source.blob_id)

    def test_provenance_names_the_parser_that_produced_it(
        self, case: tuple[Parser, SourceFile]
    ) -> None:
        parser, source = case
        document = parse(parser, source)

        assert document.provenance.parser_id == parser.id
        assert document.provenance.parser_version == parser.version
        assert document.provenance.reading_order == parser.reading_order

    def test_declared_capabilities_reach_the_document(
        self, case: tuple[Parser, SourceFile]
    ) -> None:
        parser, source = case
        document = parse(parser, source)

        assert document.provenance.capabilities == parser.capabilities.to_kernel()

    def test_geometry_is_normalized(self, case: tuple[Parser, SourceFile]) -> None:
        parser, source = case
        if not parser.capabilities.geometry:
            pytest.skip(f"{parser.id} declares no geometry")
        document = parse(parser, source)

        for token in document.tokens:
            assert token.geometry is not None
            for value in token.geometry.bbox:
                assert 0.0 <= value <= 1.0

    def test_every_token_resolves_to_a_page(self, case: tuple[Parser, SourceFile]) -> None:
        parser, source = case
        document = parse(parser, source)
        indices = {page.index for page in document.pages}

        for token in document.tokens:
            assert document.page_for(token.span)
            if token.geometry is not None:
                assert token.geometry.page_index in indices

    def test_document_carries_no_bytes(self, case: tuple[Parser, SourceFile]) -> None:
        parser, source = case
        document = parse(parser, source)

        assert not hasattr(document.source, "data")
        assert source.data[:8] not in document.text.encode()


class TestFailureBehaviour:
    def test_an_unreadable_file_raises_rather_than_returning_an_empty_document(
        self, case: tuple[Parser, SourceFile]
    ) -> None:
        """ING-7 — never a partial or empty stand-in for a document."""
        parser, source = case
        truncated = SourceFile.from_bytes(source.data[:40], filename="truncated")

        with pytest.raises(Exception) as caught:  # noqa: PT011 - any typed docdoc error
            parse(parser, truncated)

        from docdoc.kernel import DocdocError

        assert isinstance(caught.value, DocdocError), (
            f"{parser.id} leaked {type(caught.value).__name__}; provider and library "
            "exceptions must be translated (FR-025)"
        )

    def test_parsing_twice_produces_the_same_document(
        self, case: tuple[Parser, SourceFile]
    ) -> None:
        parser, source = case
        if parser.capabilities.requires_network:
            pytest.skip(f"{parser.id} is service-backed and not required to be deterministic")

        first = parse(parser, source)
        second = parse(parser, source)

        assert first.id == second.id
        assert first.text == second.text
        assert list(first.tokens) == list(second.tokens)
