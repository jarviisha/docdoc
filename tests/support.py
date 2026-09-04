"""Builders shared across the test suite.

Constructing a valid Document by hand takes a fair amount of boilerplate, which
is itself evidence for spec.md SC-010. These helpers keep individual tests
focused on the behaviour under test rather than on setup.
"""

from __future__ import annotations

from collections.abc import Sequence
from itertools import pairwise

from docdoc.extraction.adapter import ExtractionOptions, ModelUsage
from docdoc.extraction.extract import ExtractionProvenance, ExtractionResult
from docdoc.extraction.value import ExtractedValue
from docdoc.kernel import (
    BBox,
    BlobRef,
    Capabilities,
    Document,
    Geometry,
    IngestProvenance,
    Page,
    Span,
    Token,
    blob_id_for,
    options_hash_for,
)

DEFAULT_PARSER_ID = "test_parser"
DEFAULT_PARSER_VERSION = "1.0.0"


def make_blob(data: bytes = b"%PDF-1.7 test", mime_type: str = "application/pdf") -> BlobRef:
    return BlobRef(
        blob_id=blob_id_for(data),
        mime_type=mime_type,
        size_bytes=len(data),
        filename="test.pdf",
    )


def make_provenance(
    *,
    geometry: bool = True,
    tables: bool = False,
    parser_id: str = DEFAULT_PARSER_ID,
    parser_version: str = DEFAULT_PARSER_VERSION,
    options: dict[str, object] | None = None,
) -> IngestProvenance:
    opts = options if options is not None else {}
    return IngestProvenance(
        parser_id=parser_id,
        parser_version=parser_version,
        options=opts,
        options_hash=options_hash_for(opts),
        capabilities=Capabilities(text=True, geometry=geometry, tables=tables, handwriting=False),
        text_layer_used=True,
    )


def make_pages(text: str, breaks: Sequence[int] = ()) -> tuple[Page, ...]:
    """Split ``text`` into contiguous pages at the given offsets.

    Page spans must tile the text exactly (DOC-5), so this always covers
    ``[0, len(text))`` with no gaps.
    """
    bounds = [0, *sorted(breaks), len(text)]
    pages: list[Page] = []
    for index, (start, end) in enumerate(pairwise(bounds)):
        pages.append(
            Page(index=index, span=Span(start, end), width=612.0, height=792.0, rotation=0)
        )
    return tuple(pages)


def tokenize_words(
    text: str, pages: Sequence[Page], *, with_geometry: bool = True
) -> tuple[Token, ...]:
    """Produce one token per whitespace-delimited word, with synthetic geometry.

    Geometry is laid out deterministically: tokens are distributed left to right
    across a notional line per page. The exact boxes do not matter to any test;
    what matters is that they are stable and that each token maps to the page
    whose span contains it.
    """
    tokens: list[Token] = []
    position = 0
    for page in pages:
        page_text = text[page.span.start : page.span.end]
        page_token_count = max(len(page_text.split()), 1)
        seen_on_page = 0
        position = page.span.start
        while position < page.span.end:
            if text[position].isspace():
                position += 1
                continue
            end = position
            while end < page.span.end and not text[end].isspace():
                end += 1
            geometry = None
            if with_geometry:
                slot = seen_on_page / page_token_count
                width = 0.8 / page_token_count
                geometry = Geometry(
                    page_index=page.index,
                    bbox=BBox(
                        round(0.1 + slot * 0.8, 6),
                        0.1,
                        round(min(0.1 + slot * 0.8 + width, 1.0), 6),
                        0.14,
                    ),
                )
            tokens.append(
                Token(span=Span(position, end), geometry=geometry, source_confidence=0.99)
            )
            seen_on_page += 1
            position = end
    return tuple(tokens)


def make_document(
    text: str = "Invoice No: INV-001\nTotal: 125000",
    *,
    page_breaks: Sequence[int] = (),
    with_geometry: bool = True,
    tables: tuple = (),
    blocks: tuple = (),
    parser_id: str = DEFAULT_PARSER_ID,
    parser_version: str = DEFAULT_PARSER_VERSION,
    options: dict[str, object] | None = None,
    data: bytes = b"%PDF-1.7 test",
) -> Document:
    """Build a valid Document with sensible defaults."""
    pages = make_pages(text, page_breaks)
    tokens = tokenize_words(text, pages, with_geometry=with_geometry)
    return Document.create(
        text=text,
        pages=pages,
        tokens=tokens,
        blocks=blocks,
        tables=tables,
        provenance=make_provenance(
            geometry=with_geometry,
            tables=bool(tables),
            parser_id=parser_id,
            parser_version=parser_version,
            options=options,
        ),
        source=make_blob(data),
    )


# ---------------------------------------------------------------------------
# Extraction results, for the grounding suite (Milestone 4)
# ---------------------------------------------------------------------------
#
# Grounding reads an ExtractionResult and never produces one, so its tests need a
# way to state "a model claimed X for field Y" without running an extraction.
# Building one by hand is more boilerplate than building a Document.


def make_extracted(
    field_path: str,
    *,
    value: object = None,
    present: bool = True,
    claimed_text: str | None = None,
    model_confidence: float | None = None,
) -> ExtractedValue:
    """One extracted value, with the grounding fields left unresolved as Milestone 3 leaves them."""
    return ExtractedValue(
        field_path=field_path,
        value=value,
        present=present,
        claimed_text=claimed_text,
        model_confidence=model_confidence,
    )


def make_extraction(
    values: dict[str, object],
    *,
    document: Document | None = None,
    document_id: str | None = None,
    artifact_id: str = "sha256:" + "e" * 64,
    schema_identity: str = "invoice@1",
    schema_hash: str = "sha256:" + "5" * 64,
) -> ExtractionResult:
    """An ExtractionResult carrying `values`, provenanced to `document`.

    `document_id` overrides the document's own id, which is how the wrong-document
    tests build a result that plausibly belongs somewhere else. `schema_identity`
    and `schema_hash` are overridable for the same reason one milestone later:
    validation refuses a result whose recorded schema is not the one it was handed,
    so a fixture has to be able to record the right one — and the wrong one.
    """
    fallback = document.id if document else "sha256:" + "d" * 64
    doc_id = document_id if document_id is not None else fallback
    return ExtractionResult(
        values=values,
        artifact_id=artifact_id,
        provenance=ExtractionProvenance(
            document_id=doc_id,
            schema_identity=schema_identity,
            schema_hash=schema_hash,
            prompt_hash="sha256:" + "7" * 64,
            projection_id="response-shape@1",
            adapter_id="echo",
            adapter_version="1.0.0",
            model_id="echo",
            model_version="1.0.0",
            decoding=ExtractionOptions(),
            extractor_version="1.0.0+echo-1.0.0",
            usage=ModelUsage(),
        ),
    )


# -- Milestone 9: the two tenants the isolation tests use ---------------------
#
# The keys live here and their hashes live in `tests/fixtures/keys.json`, which
# is the same split a real deployment has: the file holds hashes and the keys
# are held by whoever presents them. Written down at all only because a test has
# to present one.

#: Bearer tokens for `tests/fixtures/keys.json`, by tenant.
TENANT_KEYS = {
    "acme": "acme-test-key-not-a-secret",
    "globex": "globex-test-key-not-a-secret",
}

KEYS_FILE = "tests/fixtures/keys.json"


def bearer(tenant: str) -> dict[str, str]:
    """The `Authorization` header for one of the fixture tenants."""
    return {"Authorization": f"Bearer {TENANT_KEYS[tenant]}"}
