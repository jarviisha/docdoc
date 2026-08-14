"""T012 — construction invariants DOC-1..DOC-9 (FR-007, FR-024, SC-009).

The contract these tests defend: an invalid Document cannot exist. Every
violation is caught at construction, so no later operation has to defend
against a malformed document.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from docdoc.kernel import (
    BBox,
    Block,
    BlockKind,
    Document,
    DocumentInvariantError,
    Geometry,
    IdentityError,
    Page,
    Span,
    SpanIndex,
    Table,
    TableCell,
    Token,
    document_id_for,
)
from tests.support import make_blob, make_document, make_provenance

TEXT = "Invoice INV-001"


def one_page(text: str = TEXT) -> tuple[Page, ...]:
    return (Page(index=0, span=Span(0, len(text)), width=612.0, height=792.0),)


def build(**overrides: object) -> Document:
    """Construct a document, overriding one piece at a time."""
    defaults: dict[str, object] = {
        "text": TEXT,
        "pages": one_page(),
        "tokens": (
            Token(Span(0, 7), Geometry(0, BBox(0.1, 0.1, 0.3, 0.14)), None),
            Token(Span(8, 15), Geometry(0, BBox(0.35, 0.1, 0.6, 0.14)), None),
        ),
        "provenance": make_provenance(),
        "source": make_blob(),
    }
    defaults.update(overrides)
    return Document.create(**defaults)  # type: ignore[arg-type]


class TestValidConstruction:
    def test_a_well_formed_document_is_accepted(self) -> None:
        doc = build()
        assert doc.text == TEXT
        assert len(doc.tokens) == 2

    def test_an_empty_document_is_valid(self) -> None:
        """A blank page is a normal condition, not an error."""
        doc = build(
            text="", pages=(Page(index=0, span=Span(0, 0), width=612.0, height=792.0),), tokens=()
        )
        assert doc.text == ""
        assert len(doc.tokens) == 0

    def test_a_page_with_no_tokens_is_valid(self) -> None:
        """A blank scanned page in a multi-page document."""
        text = "first page"
        pages = (
            Page(index=0, span=Span(0, 10), width=612.0, height=792.0),
            Page(index=1, span=Span(10, 10), width=612.0, height=792.0),
        )
        doc = build(
            text=text,
            pages=pages,
            tokens=(Token(Span(0, 5), Geometry(0, BBox(0.1, 0.1, 0.3, 0.14)), None),),
        )
        assert len(doc.pages) == 2


class TestDoc1TokenBounds:
    def test_token_beyond_text_is_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                tokens=(Token(Span(0, 999), None, None),),
                provenance=make_provenance(geometry=False),
            )
        assert excinfo.value.rule == "DOC-1"


class TestDoc2TokenOrdering:
    def test_unordered_tokens_are_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                tokens=(
                    Token(Span(8, 15), None, None),
                    Token(Span(0, 7), None, None),
                ),
                provenance=make_provenance(geometry=False),
            )
        assert excinfo.value.rule == "DOC-2"


class TestDoc3TokenOverlap:
    def test_overlapping_tokens_are_rejected(self) -> None:
        """Normalizing overlap is the producing layer's job, not the kernel's."""
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                tokens=(
                    Token(Span(0, 9), None, None),
                    Token(Span(5, 15), None, None),
                ),
                provenance=make_provenance(geometry=False),
            )
        assert excinfo.value.rule == "DOC-3"

    def test_adjacent_tokens_are_accepted(self) -> None:
        """Touching is not overlapping."""
        doc = build(
            tokens=(
                Token(Span(0, 7), None, None),
                Token(Span(7, 15), None, None),
            ),
            provenance=make_provenance(geometry=False),
        )
        assert len(doc.tokens) == 2


class TestDoc4PageIndices:
    def test_descending_page_indices_are_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                pages=(
                    Page(index=1, span=Span(0, 8), width=612.0, height=792.0),
                    Page(index=0, span=Span(8, 15), width=612.0, height=792.0),
                ),
                tokens=(),
                provenance=make_provenance(geometry=False),
            )
        assert excinfo.value.rule == "DOC-4"

    def test_duplicate_page_indices_are_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                pages=(
                    Page(index=0, span=Span(0, 8), width=612.0, height=792.0),
                    Page(index=0, span=Span(8, 15), width=612.0, height=792.0),
                ),
                tokens=(),
                provenance=make_provenance(geometry=False),
            )
        assert excinfo.value.rule == "DOC-4"

    def test_sparse_page_indices_are_accepted(self) -> None:
        """A slice preserves original page numbers, so gaps are legitimate.

        Requiring contiguity would force slices to renumber, and a slice of
        page 7 reporting "page 0" destroys exactly the provenance this project
        exists to preserve.
        """
        doc = build(
            pages=(
                Page(index=3, span=Span(0, 8), width=612.0, height=792.0),
                Page(index=7, span=Span(8, 15), width=612.0, height=792.0),
            ),
            tokens=(),
            provenance=make_provenance(geometry=False),
        )
        assert [p.index for p in doc.pages] == [3, 7]


class TestDoc5PageCoverage:
    def test_pages_with_a_gap_are_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                pages=(
                    Page(index=0, span=Span(0, 5), width=612.0, height=792.0),
                    Page(index=1, span=Span(9, 15), width=612.0, height=792.0),
                ),
                tokens=(),
                provenance=make_provenance(geometry=False),
            )
        assert excinfo.value.rule == "DOC-5"

    def test_pages_not_covering_the_whole_text_are_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                pages=(Page(index=0, span=Span(0, 5), width=612.0, height=792.0),),
                tokens=(),
                provenance=make_provenance(geometry=False),
            )
        assert excinfo.value.rule == "DOC-5"

    def test_overlapping_pages_are_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                pages=(
                    Page(index=0, span=Span(0, 10), width=612.0, height=792.0),
                    Page(index=1, span=Span(5, 15), width=612.0, height=792.0),
                ),
                tokens=(),
                provenance=make_provenance(geometry=False),
            )
        assert excinfo.value.rule == "DOC-5"


class TestDoc6PageReferences:
    def test_token_geometry_referencing_a_missing_page_is_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(tokens=(Token(Span(0, 7), Geometry(9, BBox(0.1, 0.1, 0.3, 0.14)), None),))
        assert excinfo.value.rule == "DOC-6"

    def test_block_referencing_a_missing_page_is_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(blocks=(Block(span=Span(0, 7), kind=BlockKind.PARAGRAPH, page_index=5),))
        assert excinfo.value.rule == "DOC-6"


class TestDoc7BlockAndTableBounds:
    def test_block_beyond_text_is_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(blocks=(Block(span=Span(0, 999), kind=BlockKind.PARAGRAPH, page_index=0),))
        assert excinfo.value.rule == "DOC-7"


class TestDoc8GeometryIsAllOrNothing:
    """Partial geometry is rejected rather than supported (plan.md design decision 1).

    Allowing it would make locate() silently lossy: a caller could not tell
    "no token there" from "geometry unavailable here".
    """

    def test_declared_geometry_with_a_token_missing_it_is_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                tokens=(
                    Token(Span(0, 7), Geometry(0, BBox(0.1, 0.1, 0.3, 0.14)), None),
                    Token(Span(8, 15), None, None),
                ),
                provenance=make_provenance(geometry=True),
            )
        assert excinfo.value.rule == "DOC-8"

    def test_undeclared_geometry_with_a_token_carrying_it_is_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                tokens=(Token(Span(0, 7), Geometry(0, BBox(0.1, 0.1, 0.3, 0.14)), None),),
                provenance=make_provenance(geometry=False),
            )
        assert excinfo.value.rule == "DOC-8"

    def test_no_geometry_declared_and_none_supplied_is_valid(self) -> None:
        doc = build(
            tokens=(Token(Span(0, 7), None, None),),
            provenance=make_provenance(geometry=False),
        )
        assert doc.provenance.capabilities.geometry is False


class TestDoc9Identity:
    def test_identity_is_derived_from_source_and_provenance(self) -> None:
        doc = build()
        expected = document_id_for(
            blob_id=doc.source.blob_id,
            parser_id=doc.provenance.parser_id,
            parser_version=doc.provenance.parser_version,
            options_hash=doc.provenance.options_hash,
        )
        assert doc.id == expected

    def test_a_mismatched_identity_is_rejected(self) -> None:
        """FR-016 — an id that does not match its derivation cannot exist."""
        doc = build()
        with pytest.raises(IdentityError) as excinfo:
            Document(
                id="sha256:" + "0" * 64,
                text=doc.text,
                pages=doc.pages,
                tokens=doc.tokens,
                blocks=doc.blocks,
                tables=doc.tables,
                provenance=doc.provenance,
                source=doc.source,
                origin=doc.origin,
            )
        assert excinfo.value.field == "id"


class TestImmutability:
    def test_fields_cannot_be_reassigned(self) -> None:
        """FR-002 — documents are immutable."""
        doc = build()
        with pytest.raises(ValidationError):
            doc.text = "tampered"  # type: ignore[misc]

    def test_document_carries_no_bytes(self) -> None:
        """FR-003 — original bytes are referenced, never held."""
        doc = build()
        assert not hasattr(doc, "data")
        assert not hasattr(doc.source, "data")


class TestNoPartialConstruction:
    def test_a_rejected_document_leaves_nothing_behind(self) -> None:
        """FR-024 — failure produces no partially built document."""
        with pytest.raises(DocumentInvariantError):
            build(
                tokens=(Token(Span(0, 999), None, None),),
                provenance=make_provenance(geometry=False),
            )
        # The valid path still works afterwards; no global state was corrupted.
        assert make_document().text


class TestUnicode:
    def test_positions_are_code_points_not_bytes(self) -> None:
        """FR-004 — Vietnamese text must not shift offsets."""
        text = "Công ty ABC"
        doc = build(
            text=text,
            pages=(Page(index=0, span=Span(0, len(text)), width=612.0, height=792.0),),
            tokens=(Token(Span(0, 4), None, None),),
            provenance=make_provenance(geometry=False),
        )
        assert doc.text[0:4] == "Công"
        assert len(doc.text) == 11

    def test_characters_outside_the_bmp_are_handled(self) -> None:
        text = "total 🧾 125000"
        doc = build(
            text=text,
            pages=(Page(index=0, span=Span(0, len(text)), width=612.0, height=792.0),),
            tokens=(Token(Span(6, 7), None, None),),
            provenance=make_provenance(geometry=False),
        )
        assert doc.text[6:7] == "🧾"


class TestRemainingInvariantPaths:
    """Coverage for validator branches the happy-path tests do not reach."""

    def test_text_without_any_page_is_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(pages=(), tokens=(), provenance=make_provenance(geometry=False))
        assert excinfo.value.rule == "DOC-5"

    def test_token_anchored_to_a_page_that_does_not_contain_it_is_rejected(self) -> None:
        """A token cannot physically sit on a page whose text excludes it."""
        text = "first second"
        pages = (
            Page(index=0, span=Span(0, 6), width=612.0, height=792.0),
            Page(index=1, span=Span(6, 12), width=612.0, height=792.0),
        )
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                text=text,
                pages=pages,
                tokens=(Token(Span(7, 12), Geometry(0, BBox(0.1, 0.1, 0.3, 0.14)), None),),
            )
        assert excinfo.value.rule == "DOC-6"

    def test_table_referencing_a_missing_page_is_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                tables=(Table(span=Span(0, 7), page_index=9, n_rows=1, n_columns=1),),
            )
        assert excinfo.value.rule == "DOC-6"

    def test_table_beyond_the_text_is_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(tables=(Table(span=Span(0, 999), page_index=0, n_rows=1, n_columns=1),))
        assert excinfo.value.rule == "DOC-7"

    def test_table_cell_beyond_the_text_is_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                tables=(
                    Table(
                        span=Span(0, 7),
                        page_index=0,
                        n_rows=1,
                        n_columns=1,
                        cells=(TableCell(span=Span(0, 999), row=0, column=0),),
                    ),
                )
            )
        assert excinfo.value.rule == "DOC-7"


class TestDoc10Origin:
    """Origin ranges say which parts of the original parse this document holds."""

    def test_unordered_origin_ranges_are_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                tokens=(),
                provenance=make_provenance(geometry=False),
                origin=(Span(8, 15), Span(0, 7)),
            )
        assert excinfo.value.rule == "DOC-10"

    def test_origin_not_accounting_for_all_text_is_rejected(self) -> None:
        with pytest.raises(DocumentInvariantError) as excinfo:
            build(
                tokens=(),
                provenance=make_provenance(geometry=False),
                origin=(Span(0, 3),),
            )
        assert excinfo.value.rule == "DOC-10"

    def test_a_fresh_document_covers_its_whole_text(self) -> None:
        doc = build()
        assert doc.origin == (Span(0, len(TEXT)),)


class TestTokenCoercion:
    def test_a_plain_sequence_of_tokens_is_accepted(self) -> None:
        """Callers should not have to build a SpanIndex by hand."""
        reference = build()
        doc = Document(
            id=reference.id,
            text=reference.text,
            pages=reference.pages,
            tokens=list(reference.tokens),
            provenance=reference.provenance,
            source=reference.source,
            origin=reference.origin,
        )
        assert isinstance(doc.tokens, SpanIndex)
        assert tuple(doc.tokens) == tuple(reference.tokens)
