"""T040 — the two-level identity contract (US3, FR-015..FR-017, SC-003, SC-004).

`test_identity.py` covers the identity *functions* in isolation. This module
covers the *contract* they exist to uphold: two parses of one file must be
distinguishable, because their text positions are not interchangeable.

That last point is FR-017, and it is the reason the two levels exist at all.
"""

from __future__ import annotations

from docdoc.kernel import (
    BlobRef,
    Capabilities,
    Document,
    IngestProvenance,
    Page,
    Span,
    Token,
    blob_id_for,
    document_id_for,
    options_hash_for,
)

RAW = b"%PDF-1.7 the same bytes for both parsers"


def parse_as(text: str, *, parser_id: str, parser_version: str = "1.0.0") -> Document:
    """One parse of RAW, as some parser would produce it.

    Different parsers legitimately produce different canonical text from the
    same bytes -- different whitespace handling, different reading order,
    different ligature treatment. That is exactly why their spans cannot be
    shared.
    """
    options: dict[str, object] = {}
    return Document.create(
        text=text,
        pages=(Page(index=0, span=Span(0, len(text)), width=612.0, height=792.0),),
        tokens=tuple(
            Token(span=Span(start, start + len(word)))
            for word, start in _word_offsets(text)
        ),
        provenance=IngestProvenance(
            parser_id=parser_id,
            parser_version=parser_version,
            options=options,
            options_hash=options_hash_for(options),
            capabilities=Capabilities(
                text=True, geometry=False, tables=False, handwriting=False
            ),
            text_layer_used=True,
        ),
        source=BlobRef(
            blob_id=blob_id_for(RAW), mime_type="application/pdf", size_bytes=len(RAW)
        ),
    )


def _word_offsets(text: str) -> list[tuple[str, int]]:
    result: list[tuple[str, int]] = []
    position = 0
    for word in text.split(" "):
        if word:
            result.append((word, position))
        position += len(word) + 1
    return result


class TestUserStory3:
    """The five acceptance scenarios from spec.md US3."""

    def test_the_same_bytes_always_yield_the_same_source_identity(self) -> None:
        """Scenario 1 — SC-003."""
        assert blob_id_for(RAW) == blob_id_for(RAW)

    def test_two_parsers_share_a_blob_id_but_differ_in_document_id(self) -> None:
        """Scenario 2 — the heart of ADR-0002."""
        fast = parse_as("Invoice INV-001", parser_id="pdf_text")
        good = parse_as("Invoice  INV-001", parser_id="cloud_di")

        assert fast.source.blob_id == good.source.blob_id
        assert fast.id != good.id

    def test_the_same_configuration_yields_the_same_document_id(self) -> None:
        """Scenario 3."""
        first = parse_as("Invoice INV-001", parser_id="pdf_text")
        second = parse_as("Invoice INV-001", parser_id="pdf_text")
        assert first.id == second.id

    def test_option_key_order_does_not_change_identity(self) -> None:
        """Scenario 4 — SC-004."""
        assert options_hash_for({"dpi": 300, "lang": "vi"}) == options_hash_for(
            {"lang": "vi", "dpi": 300}
        )

    def test_a_parser_version_bump_changes_the_document_id(self) -> None:
        """Scenario 5 — a fixed parser is a different parser."""
        before = parse_as("Invoice INV-001", parser_id="pdf_text", parser_version="1.0.0")
        after = parse_as("Invoice INV-001", parser_id="pdf_text", parser_version="1.1.0")
        assert before.source.blob_id == after.source.blob_id
        assert before.id != after.id


class TestSpansAreRelativeToTheParse:
    """FR-017 — spans are interpreted relative to `document_id`, never to
    `blob_id` alone.

    This is the failure the two-level model exists to prevent: under a
    bytes-only identity, nothing would stop a caller applying one parse's span
    to another, and the result would be a confidently wrong location rather
    than an error.
    """

    def test_the_same_value_sits_at_different_offsets_in_different_parses(self) -> None:
        # One parser collapses the double space, the other preserves it.
        collapsed = parse_as("Invoice INV-001", parser_id="pdf_text")
        preserved = parse_as("Invoice  INV-001", parser_id="cloud_di")

        (a,) = collapsed.find("INV-001")
        (b,) = preserved.find("INV-001")

        assert a != b, "the premise of this test requires the offsets to differ"

    def test_a_span_from_one_parse_names_different_text_in_another(self) -> None:
        """The concrete harm: reusing a span silently reads the wrong characters."""
        collapsed = parse_as("Invoice INV-001", parser_id="pdf_text")
        preserved = parse_as("Invoice  INV-001", parser_id="cloud_di")

        (span,) = collapsed.find("INV-001")
        borrowed = preserved.text[span.start : span.end]

        assert borrowed != "INV-001"

    def test_documents_sharing_a_blob_id_are_still_distinguishable(self) -> None:
        """A caller can always tell two parses apart before trusting a span."""
        collapsed = parse_as("Invoice INV-001", parser_id="pdf_text")
        preserved = parse_as("Invoice  INV-001", parser_id="cloud_di")

        assert collapsed.source.blob_id == preserved.source.blob_id
        assert collapsed.id != preserved.id
        # The check a caller must make: compare document ids, not blob ids.
        assert (collapsed.id == preserved.id) is False

    def test_identity_is_derivable_from_source_and_provenance_alone(self) -> None:
        """A stored result can be matched back to its parse without the document."""
        document = parse_as("Invoice INV-001", parser_id="pdf_text")
        assert document.id == document_id_for(
            blob_id=document.source.blob_id,
            parser_id=document.provenance.parser_id,
            parser_version=document.provenance.parser_version,
            options_hash=document.provenance.options_hash,
        )
