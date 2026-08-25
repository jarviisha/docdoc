"""The canonical Document Intermediate Representation.

A ``Document`` is immutable and self-consistent: every invariant in
data-model.md (DOC-1..DOC-9) is checked once, at construction, so **an invalid
document cannot exist**. No operation downstream has to defend against a
malformed one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, model_validator

from docdoc.kernel.blob import BlobRef
from docdoc.kernel.block import Block
from docdoc.kernel.errors import (
    CapabilityError,
    DocumentInvariantError,
    IdentityError,
    MergeError,
    SpanError,
)
from docdoc.kernel.identity import document_id_for
from docdoc.kernel.page import Page
from docdoc.kernel.provenance import IngestProvenance
from docdoc.kernel.span import Span
from docdoc.kernel.span_index import SpanIndex
from docdoc.kernel.table import Table
from docdoc.kernel.token import Token

if TYPE_CHECKING:
    from collections.abc import Sequence

    from docdoc.kernel.geometry import Geometry

__all__ = ["Document"]


class Document(BaseModel):
    """Canonical text plus everything needed to trace it back to its source.

    Identity is two-level (ADR-0002): ``source.blob_id`` identifies the file,
    while ``id`` identifies *this parse* of it. All spans and geometry are
    interpreted relative to ``id``, never to ``blob_id`` alone.
    """

    model_config = ConfigDict(frozen=True, arbitrary_types_allowed=True, extra="forbid")

    id: str
    text: str
    pages: tuple[Page, ...]
    tokens: SpanIndex
    blocks: tuple[Block, ...] = ()
    tables: tuple[Table, ...] = ()
    provenance: IngestProvenance
    source: BlobRef

    #: Which ranges of the *originally parsed* text this document occupies, in
    #: order. A freshly parsed document holds a single span covering everything.
    #: Slicing narrows it; merging concatenates the parts' ranges.
    #:
    #: This exists because ``merge`` cannot otherwise tell whether two parts
    #: overlap or which order they belong in -- both of which it must know to
    #: avoid duplicating tokens and to keep pages in reading order. Without it,
    #: the rejection rules in contracts/kernel-api.md are not implementable.
    origin: tuple[Span, ...] = ()

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def create(
        cls,
        *,
        text: str,
        pages: Sequence[Page],
        tokens: Sequence[Token] | SpanIndex,
        provenance: IngestProvenance,
        source: BlobRef,
        blocks: Sequence[Block] = (),
        tables: Sequence[Table] = (),
        origin: Sequence[Span] | None = None,
    ) -> Document:
        """Build a document, deriving its identity from source and provenance.

        This is the normal entry point. The bare constructor is available for
        callers that already hold a derived ``id``, and validates that it
        matches (DOC-9).
        """
        return cls(
            id=document_id_for(
                blob_id=source.blob_id,
                parser_id=provenance.parser_id,
                parser_version=provenance.parser_version,
                options_hash=provenance.options_hash,
            ),
            text=text,
            pages=tuple(pages),
            tokens=tokens if isinstance(tokens, SpanIndex) else SpanIndex(tokens),
            blocks=tuple(blocks),
            tables=tuple(tables),
            provenance=provenance,
            source=source,
            origin=tuple(origin) if origin is not None else (Span(0, len(text)),),
        )

    @model_validator(mode="before")
    @classmethod
    def _coerce_tokens(cls, data: Any) -> Any:
        """Accept a sequence of ``Token`` where a ``SpanIndex`` is declared.

        Only for tokens that are *already* ``Token`` objects. Raw JSON — a list
        of lists, which is how a ``NamedTuple`` serialises — is left alone for
        ``SpanIndex``'s own core schema to validate, because ``SpanIndex(...)``
        cannot build itself from unvalidated data and would fail reaching for
        ``.span`` on a list.

        That distinction only started mattering in Milestone 7, when the parse
        stage became an artifact that has to survive a round trip through the
        store.
        """
        if isinstance(data, dict):
            tokens = data.get("tokens")
            if tokens is not None and not isinstance(tokens, SpanIndex):
                # The *first* element decides, not all of them. A document can
                # carry fifty thousand tokens and this runs on every
                # construction, so an `all(...)` here is an O(n) scan on the hot
                # path — which is precisely what it was, and what the kernel
                # performance budget caught. A token sequence is homogeneous:
                # either the caller built `Token`s or pydantic is handing over
                # raw JSON, and one look tells which.
                first = next(iter(tokens), None)
                if first is None or isinstance(first, Token):
                    data = {**data, "tokens": SpanIndex(tokens)}
        return data

    @model_validator(mode="after")
    def _check_invariants(self) -> Document:
        self._check_pages()
        self._check_tokens()
        self._check_page_references()
        self._check_block_and_table_bounds()
        self._check_geometry_is_all_or_nothing()
        self._check_origin()
        self._check_identity()
        return self

    def _check_origin(self) -> None:
        """DOC-10 — origin ranges are ordered, disjoint, and account for all text."""
        total = 0
        previous: Span | None = None
        for piece in self.origin:
            if previous is not None and piece.start < previous.end:
                raise DocumentInvariantError(
                    "origin ranges must be ordered and disjoint",
                    rule="DOC-10",
                    detail=f"{piece.start}:{piece.end} follows {previous.start}:{previous.end}",
                )
            total += len(piece)
            previous = piece
        if total != len(self.text):
            raise DocumentInvariantError(
                "origin ranges must account for exactly the document's text",
                rule="DOC-10",
                detail=f"origin covers {total} characters, text has {len(self.text)}",
            )

    # ------------------------------------------------------------------
    # Operations
    # ------------------------------------------------------------------

    def locate(self, span: Span) -> tuple[Geometry, ...]:
        """Resolve a text range to the physical locations it occupies.

        Returns one :class:`Geometry` per token **intersecting** ``span``, in
        document order, using each token's full bounding box.

        Two deliberate choices, both from research.md:

        * **No sub-token interpolation** (R7). Parsers report geometry per
          token; deriving a partial box from a character offset would assume
          uniform glyph advance, which is false for proportional fonts and for
          every complex script. Returning the containing token is coarse but
          never wrong.
        * **No grouping** (R8). Merging boxes into per-line or per-page
          rectangles needs a vertical-overlap heuristic with a tunable
          threshold, which has no place in a deterministic kernel. A union per
          page would be actively misleading: a multi-line span would become one
          rectangle covering unrelated text.

        Raises:
            SpanError: the span does not fit this document's text.
            CapabilityError: the producing parser supplied no geometry. This is
                raised rather than returning an empty tuple, so a caller can
                never mistake "unavailable" for "nothing there" (FR-022).
        """
        span.validate_within(len(self.text))
        if not self.provenance.capabilities.geometry:
            raise CapabilityError(
                "this document has no geometry; its parser did not supply any",
                capability="geometry",
                available=False,
                parser_id=self.provenance.parser_id,
            )
        return tuple(
            token.geometry for token in self.tokens.tokens_in(span) if token.geometry is not None
        )

    def page_for(self, span: Span) -> tuple[int, ...]:
        """Resolve a text range to the pages it falls on, in ascending order.

        Unlike :meth:`locate`, this works when the parser supplied no geometry:
        pages tile the text exactly (DOC-5), so a span's pages are determined by
        text position alone. FR-006 requires every token be traceable to a page,
        and geometry is not always available to carry that.

        An empty span occupies no positions and therefore no pages.

        Raises:
            SpanError: the span does not fit this document's text.
        """
        span.validate_within(len(self.text))
        if span.is_empty:
            return ()
        return tuple(page.index for page in self.pages if page.span.intersects(span))

    def find(self, text: str) -> tuple[Span, ...]:
        """Every exact occurrence of ``text``, in document order.

        Matching is literal and **exact only**: no case folding, no Unicode
        normalization, no whitespace collapsing, and no fuzzy parameter. Fuzzy
        matching lives in the extraction layer, because the kernel cannot host
        it without breaking its dependency rule (ADR-0005).

        Overlapping candidates resolve left to right, resuming after each match,
        so ``"aaa"`` in ``"aaaaa"`` yields one match rather than three.

        An absent string yields ``()`` -- a normal outcome, not an error.

        Raises:
            SpanError: ``text`` is empty, which would otherwise match at every
                position and mean nothing.
        """
        if not text:
            raise SpanError("search text must not be empty")

        matches: list[Span] = []
        cursor = 0
        width = len(text)
        while True:
            position = self.text.find(text, cursor)
            if position < 0:
                break
            matches.append(Span(position, position + width))
            cursor = position + width
        return tuple(matches)

    def slice(self, span: Span) -> Document:
        """A new document covering ``span``.

        Tokens **fully contained** in ``span`` are retained and rebased; tokens
        the cut would truncate are **dropped**. Keeping a clipped token would
        leave its geometry describing glyphs no longer present in the sliced
        text -- a silently wrong box, which is worse than a missing one.

        Page numbers are preserved rather than renumbered, so a slice of page 7
        still reports page 7. Geometry is therefore carried through byte-identical,
        which is what makes the round-trip invariant hold.

        Raises:
            SpanError: the span does not fit this document's text.
        """
        span.validate_within(len(self.text))
        offset = span.start

        pages = tuple(
            page.model_copy(
                update={
                    "span": Span(
                        max(page.span.start, span.start) - offset,
                        min(page.span.end, span.end) - offset,
                    )
                }
            )
            for page in self.pages
            if page.span.intersects(span)
        )

        tokens = tuple(
            token._replace(span=token.span.shift(-offset))
            for token in self.tokens
            if span.contains_span(token.span) and not token.span.is_empty
        )

        blocks = tuple(
            block.model_copy(update={"span": block.span.shift(-offset)})
            for block in self.blocks
            if span.contains_span(block.span)
        )

        tables = tuple(
            table.model_copy(
                update={
                    "span": table.span.shift(-offset),
                    "cells": tuple(
                        cell.model_copy(update={"span": cell.span.shift(-offset)})
                        for cell in table.cells
                    ),
                }
            )
            for table in self.tables
            if span.contains_span(table.span)
        )

        return Document.create(
            text=self.text[span.start : span.end],
            pages=pages,
            tokens=tokens,
            blocks=blocks,
            tables=tables,
            provenance=self.provenance,
            source=self.source,
            origin=self._origin_of(span),
        )

    @classmethod
    def merge(cls, parts: Sequence[Document]) -> Document:
        """Reassemble parts into one document, rebasing text positions.

        Every part must come from the same file and the same producing
        configuration, and their original ranges must not overlap. Parts must be
        supplied in ascending original order.

        Token geometry is carried through unchanged and page numbers are
        preserved, so every token still resolves to its true original page and
        box. Non-adjacent parts are allowed: the merged *text* is a working
        buffer that never existed contiguously in the source, while the
        *geometry* remains true. That is the trade research.md R6 accepts, and it
        is what keeps windowed extraction possible.

        Raises:
            MergeError: no parts, mismatched source or parser, overlapping
                original ranges, or parts out of order.
        """
        parts = tuple(parts)
        if not parts:
            raise MergeError(
                "cannot merge zero parts: there is no source or provenance to carry",
                reason="no_parts",
            )

        first = parts[0]
        for part in parts[1:]:
            if (
                part.source.blob_id != first.source.blob_id
                or part.provenance.parser_id != first.provenance.parser_id
                or part.provenance.parser_version != first.provenance.parser_version
            ):
                raise MergeError(
                    "parts must come from the same file and the same parser version",
                    reason="mismatched_source",
                    part_ids=tuple(p.id for p in parts),
                )

        origin: list[Span] = []
        for part in parts:
            for candidate in part.origin:
                if origin and candidate.start < origin[-1].end:
                    reason = (
                        "overlapping_parts"
                        if candidate.start < origin[-1].end and candidate.end > origin[-1].start
                        else "unordered_parts"
                    )
                    raise MergeError(
                        "parts must not overlap and must be supplied in ascending "
                        f"original order (got {candidate.start}:{candidate.end} after "
                        f"{origin[-1].start}:{origin[-1].end})",
                        reason=reason,
                        part_ids=tuple(p.id for p in parts),
                    )
                origin.append(candidate)

        text_parts: list[str] = []
        tokens: list[Token] = []
        blocks: list[Block] = []
        tables: list[Table] = []
        page_spans: dict[int, list[Span]] = {}
        page_meta: dict[int, Page] = {}

        offset = 0
        for part in parts:
            text_parts.append(part.text)
            tokens.extend(token._replace(span=token.span.shift(offset)) for token in part.tokens)
            blocks.extend(
                block.model_copy(update={"span": block.span.shift(offset)}) for block in part.blocks
            )
            tables.extend(
                table.model_copy(
                    update={
                        "span": table.span.shift(offset),
                        "cells": tuple(
                            cell.model_copy(update={"span": cell.span.shift(offset)})
                            for cell in table.cells
                        ),
                    }
                )
                for table in part.tables
            )
            for page in part.pages:
                page_spans.setdefault(page.index, []).append(page.span.shift(offset))
                page_meta.setdefault(page.index, page)
            offset += len(part.text)

        pages = tuple(
            page_meta[index].model_copy(
                update={
                    "span": Span(
                        min(s.start for s in page_spans[index]),
                        max(s.end for s in page_spans[index]),
                    )
                }
            )
            for index in sorted(page_spans)
        )

        return cls.create(
            text="".join(text_parts),
            pages=pages,
            tokens=tuple(tokens),
            blocks=tuple(blocks),
            tables=tuple(tables),
            provenance=first.provenance,
            source=first.source,
            origin=tuple(origin),
        )

    def _origin_of(self, local: Span) -> tuple[Span, ...]:
        """Map a range in this document's coordinates back to original ranges.

        A document assembled from non-adjacent parts occupies several disjoint
        ranges of the original, so a single local range can map to more than one.
        """
        result: list[Span] = []
        cursor = 0
        for piece in self.origin:
            piece_length = len(piece)
            piece_local = Span(cursor, cursor + piece_length)
            if piece_local.intersects(local):
                start = max(piece_local.start, local.start) - cursor + piece.start
                end = min(piece_local.end, local.end) - cursor + piece.start
                result.append(Span(start, end))
            cursor += piece_length
        return tuple(result)

    # ------------------------------------------------------------------
    # Invariants
    # ------------------------------------------------------------------

    def _check_pages(self) -> None:
        """DOC-4 and DOC-5 — pages are contiguous and tile the text exactly."""
        if not self.pages:
            if self.text:
                raise DocumentInvariantError(
                    "a document with text must have at least one page",
                    rule="DOC-5",
                    detail=f"text length {len(self.text)}, 0 pages",
                )
            return

        # Page indices are strictly ascending and unique, but NOT required to be
        # contiguous from 0. Slicing preserves original page numbers -- a slice
        # of page 7 must still report page 7 -- so a sliced document legitimately
        # holds a sparse index set. A freshly parsed document is contiguous
        # because its parser numbers pages from 0.
        previous_index: int | None = None
        for page in self.pages:
            if previous_index is not None and page.index <= previous_index:
                raise DocumentInvariantError(
                    "page indices must be strictly ascending and unique",
                    rule="DOC-4",
                    detail=f"page index {page.index} follows {previous_index}",
                )
            previous_index = page.index

        cursor = 0
        for page in self.pages:
            if page.span.start != cursor:
                raise DocumentInvariantError(
                    "page spans must tile the text with no gap or overlap",
                    rule="DOC-5",
                    detail=f"page {page.index} starts at {page.span.start}, expected {cursor}",
                )
            cursor = page.span.end
        if cursor != len(self.text):
            raise DocumentInvariantError(
                "page spans must cover the whole text",
                rule="DOC-5",
                detail=f"pages cover {cursor} characters, text has {len(self.text)}",
            )

    def _check_tokens(self) -> None:
        """DOC-1, DOC-2, DOC-3 — tokens fit the text, are ordered, and never overlap."""
        text_length = len(self.text)
        previous: Token | None = None
        for position, token in enumerate(self.tokens):
            if token.span.start < 0 or token.span.end > text_length:
                raise DocumentInvariantError(
                    "token span falls outside the document text",
                    rule="DOC-1",
                    detail=(
                        f"token {position} spans {token.span.start}:{token.span.end}, "
                        f"text length is {text_length}"
                    ),
                )
            if previous is not None:
                if token.span.start < previous.span.start:
                    raise DocumentInvariantError(
                        "tokens must be ordered by ascending start offset",
                        rule="DOC-2",
                        detail=(
                            f"token {position} starts at {token.span.start}, "
                            f"after a token starting at {previous.span.start}"
                        ),
                    )
                if token.span.start < previous.span.end:
                    raise DocumentInvariantError(
                        "tokens must not overlap",
                        rule="DOC-3",
                        detail=(
                            f"token {position} starts at {token.span.start}, "
                            f"inside the previous token ending at {previous.span.end}"
                        ),
                    )
            previous = token

    def _check_page_references(self) -> None:
        """DOC-6 — every page reference resolves, and tokens stay on their page."""
        by_index = {page.index: page for page in self.pages}

        def require_page(page_index: int, what: str) -> Page:
            page = by_index.get(page_index)
            if page is None:
                raise DocumentInvariantError(
                    f"{what} references a page that does not exist",
                    rule="DOC-6",
                    detail=f"page_index {page_index}, document has pages {sorted(by_index)}",
                )
            return page

        for position, token in enumerate(self.tokens):
            if token.geometry is None:
                continue
            page = require_page(token.geometry.page_index, f"token {position}")
            if not page.span.contains_span(token.span):
                raise DocumentInvariantError(
                    "token is anchored to a page that does not contain its text",
                    rule="DOC-6",
                    detail=(
                        f"token {position} spans {token.span.start}:{token.span.end}, "
                        f"page {page.index} spans {page.span.start}:{page.span.end}"
                    ),
                )

        for position, block in enumerate(self.blocks):
            require_page(block.page_index, f"block {position}")
        for position, table in enumerate(self.tables):
            require_page(table.page_index, f"table {position}")

    def _check_block_and_table_bounds(self) -> None:
        """DOC-7 — block and table spans lie within the text."""
        text_length = len(self.text)

        def require_within(span: Span, what: str) -> None:
            if span.start < 0 or span.end > text_length:
                raise DocumentInvariantError(
                    f"{what} span falls outside the document text",
                    rule="DOC-7",
                    detail=f"spans {span.start}:{span.end}, text length is {text_length}",
                )

        for position, block in enumerate(self.blocks):
            require_within(block.span, f"block {position}")
        for position, table in enumerate(self.tables):
            require_within(table.span, f"table {position}")
            for cell_position, cell in enumerate(table.cells):
                require_within(cell.span, f"table {position} cell {cell_position}")

    def _check_geometry_is_all_or_nothing(self) -> None:
        """DOC-8 — geometry is uniform across a document's tokens.

        Partial geometry is rejected rather than supported. Allowing it would
        make ``locate()`` silently lossy: a caller could not distinguish "no
        token there" from "geometry unavailable here". A parser with partial
        geometry must declare ``capabilities.geometry = False`` and supply none.
        """
        declared = self.provenance.capabilities.geometry
        for position, token in enumerate(self.tokens):
            has_geometry = token.geometry is not None
            if has_geometry != declared:
                raise DocumentInvariantError(
                    "geometry must be present for every token or for none",
                    rule="DOC-8",
                    detail=(
                        f"capabilities.geometry is {declared}, but token {position} "
                        f"{'has' if has_geometry else 'lacks'} geometry"
                    ),
                )

    def _check_identity(self) -> None:
        """DOC-9 — the id must match its derivation from source and provenance."""
        expected = document_id_for(
            blob_id=self.source.blob_id,
            parser_id=self.provenance.parser_id,
            parser_version=self.provenance.parser_version,
            options_hash=self.provenance.options_hash,
        )
        if self.id != expected:
            raise IdentityError(
                "document id does not match its derivation from source and provenance",
                field="id",
                detail=f"expected {expected}, got {self.id}",
            )
