"""Holding a parser to what it declared.

The kernel already rejects an invalid Document; these checks run first so the
failure names the *parser* rather than surfacing as an anonymous invariant
error. That difference matters when the parser is a remote service whose output
you cannot read directly.

Nothing here repairs anything. Ordering is the adapter's responsibility (R5), so
out-of-order output is a defect to report, never something to quietly sort into
a plausible-looking result (FR-037, ING-8).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from docdoc.ingest.errors import ParserError

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docdoc.ingest.capabilities import ParserCapabilities
    from docdoc.kernel import Document, TextLayerRecord, Token

__all__ = [
    "check_capability_honesty",
    "check_corresponds_to_input",
    "check_token_order",
    "validate_output",
]


def check_token_order(tokens: Sequence[Token], *, parser_id: str, blob_id: str | None) -> None:
    """ING-8 — ascending and non-overlapping, in the order the parser emitted."""
    previous: Token | None = None
    for position, token in enumerate(tokens):
        if previous is not None and token.span.start < previous.span.end:
            raise ParserError(
                f"parser {parser_id!r} emitted tokens out of order or overlapping",
                reason="invalid_order",
                parser_id=parser_id,
                blob_id=blob_id,
                detail=(
                    f"token {position} starts at {token.span.start}, "
                    f"before the previous token ends at {previous.span.end}"
                ),
            )
        previous = token


def check_capability_honesty(
    document: Document,
    capabilities: ParserCapabilities,
    *,
    parser_id: str,
    blob_id: str | None,
) -> None:
    """ING-4 — what was declared and what was produced must agree.

    Checked in both directions. A parser that under-declares is as much of a
    problem as one that over-declares: a caller who asked for geometry and got a
    parser claiming none would never look for the geometry that is actually
    there.

    Three of the four capabilities are checkable from the output. ``handwriting``
    is not: the IR carries no marker distinguishing a recognized handwritten word
    from a printed one, so there is nothing to compare a declaration against.
    Saying so is better than implying a check that does not happen -- if
    handwriting ever becomes visible in the IR, this is where it belongs.
    """
    has_geometry = any(token.geometry is not None for token in document.tokens)
    all_geometry = all(token.geometry is not None for token in document.tokens)
    has_tokens = len(document.tokens) > 0

    if not capabilities.text and (has_tokens or document.text):
        raise ParserError(
            f"parser {parser_id!r} declares no text but produced some",
            reason="capability_mismatch",
            parser_id=parser_id,
            blob_id=blob_id,
            detail=f"{len(document.tokens)} tokens, {len(document.text)} characters",
        )
    if capabilities.geometry and has_tokens and not all_geometry:
        raise ParserError(
            f"parser {parser_id!r} declares geometry but emitted tokens without it",
            reason="capability_mismatch",
            parser_id=parser_id,
            blob_id=blob_id,
            detail="geometry is all-or-nothing; a parser with partial geometry must declare none",
        )
    if not capabilities.geometry and has_geometry:
        raise ParserError(
            f"parser {parser_id!r} declares no geometry but emitted some",
            reason="capability_mismatch",
            parser_id=parser_id,
            blob_id=blob_id,
            detail="a caller told there is no geometry will never ask for it",
        )
    if not capabilities.tables and document.tables:
        raise ParserError(
            f"parser {parser_id!r} declares no tables but emitted some",
            reason="capability_mismatch",
            parser_id=parser_id,
            blob_id=blob_id,
            detail=f"{len(document.tables)} tables present",
        )


def check_corresponds_to_input(
    document: Document,
    *,
    parser_id: str,
    blob_id: str | None = None,
    parser_version: str | None = None,
    text_layer: TextLayerRecord | None = None,
) -> None:
    """The document must be *of* the file that was handed over.

    Nothing else in the layer establishes this. A parser is given bytes and hands
    back a `Document`; that the two are related was, until now, taken on trust —
    and the `Parser` protocol openly invites third parties, so trust is the wrong
    mechanism. A buggy adapter, or one with a caching key defect, could return
    another file's document and every span downstream would point into a
    stranger's contract while `document_id` certified it (ADR-0002, Principle I).

    Each check is skipped when its expected value was not supplied, so a caller
    verifying only what it knows still gets what it can.
    """
    if blob_id is not None and document.source.blob_id != blob_id:
        raise ParserError(
            f"parser {parser_id!r} returned a document for a different file",
            reason="wrong_document",
            parser_id=parser_id,
            blob_id=blob_id,
            detail=f"document is of {document.source.blob_id}, input was {blob_id}",
        )
    if document.provenance.parser_id != parser_id:
        raise ParserError(
            f"parser {parser_id!r} returned a document attributed to "
            f"{document.provenance.parser_id!r}",
            reason="wrong_document",
            parser_id=parser_id,
            blob_id=blob_id,
            detail="provenance must name the parser that produced the document",
        )
    if parser_version is not None and document.provenance.parser_version != parser_version:
        raise ParserError(
            f"parser {parser_id!r} returned a document recording version "
            f"{document.provenance.parser_version!r}",
            reason="wrong_document",
            parser_id=parser_id,
            blob_id=blob_id,
            detail=f"the parser that ran declares {parser_version!r}",
        )
    if text_layer is not None and document.provenance.text_layer != text_layer:
        raise ParserError(
            f"parser {parser_id!r} returned a document carrying a different "
            "text-layer verdict than the one it was routed with",
            reason="wrong_document",
            parser_id=parser_id,
            blob_id=blob_id,
            detail="the verdict explains the routing; a parser may record it, not rewrite it",
        )


def validate_output(
    document: Document,
    capabilities: ParserCapabilities,
    *,
    parser_id: str,
    blob_id: str | None = None,
    parser_version: str | None = None,
    text_layer: TextLayerRecord | None = None,
) -> Document:
    """Run every parser-output check and return the document unchanged."""
    check_token_order(list(document.tokens), parser_id=parser_id, blob_id=blob_id)
    check_capability_honesty(document, capabilities, parser_id=parser_id, blob_id=blob_id)
    check_corresponds_to_input(
        document,
        parser_id=parser_id,
        blob_id=blob_id,
        parser_version=parser_version,
        text_layer=text_layer,
    )
    return document
