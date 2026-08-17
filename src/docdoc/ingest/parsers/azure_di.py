"""Geometry-capable cloud document-intelligence adapter (Azure).

The recognition path: scanned PDFs, photographs, and anything else with no
usable text layer (ADR-0001). Optional, installed via ``docdoc[azure]``.

This module is the only place an ``azure`` import may appear, and no service
type may leave it -- what comes out is a kernel ``Document`` and, on failure, a
docdoc error with the original attached as ``__cause__`` (Principle IV, FR-025).

**Text comes from the service, not from re-assembly.** Unlike the native path,
the service returns a ``content`` string *and* offsets into it for every word,
line, and table cell. Using them directly makes the correspondence exact and
lets table cells resolve to spans at all -- re-assembling text from words would
throw away the offsets that make tables placeable (research.md R6 covers the
native path; this is the same principle applied to a source that supplies its
own offsets).

Offsets are requested as ``unicodeCodePoint`` so they index Python strings
directly. The service's default (``textElements``, grapheme clusters) would drift
from Python indexing on combining marks and astral characters -- silently, and
only for some documents, which is the worst kind of drift.
"""

from __future__ import annotations

import os
import random
import time
from typing import TYPE_CHECKING, Any, Final

from docdoc.ingest.capabilities import ParserCapabilities
from docdoc.ingest.errors import ParserError, ProviderError, UnsupportedDocumentError
from docdoc.ingest.normalize import normalize_bbox
from docdoc.ingest.options import options_fingerprint
from docdoc.ingest.source import JPEG, PDF, PNG
from docdoc.kernel import (
    DocdocError,
    Document,
    Geometry,
    GeometryError,
    IngestProvenance,
    Page,
    Span,
    Table,
    TableCell,
    Token,
)

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from docdoc.ingest.options import Deadline, TransportSettings
    from docdoc.ingest.source import SourceFile
    from docdoc.kernel import TextLayerRecord

__all__ = ["AzureDocumentIntelligenceParser", "map_analyze_result"]

PARSER_ID: Final = "azure-di"
ADAPTER_VERSION: Final = "1.0.0"
SERVICE_API_VERSION: Final = "2024-11-30"

ENDPOINT_ENV: Final = "DOCDOC_AZURE_DI_ENDPOINT"
KEY_ENV: Final = "DOCDOC_AZURE_DI_KEY"

#: HTTP statuses worth another attempt. Everything else is permanent.
_TRANSIENT_STATUS: Final = frozenset({408, 429, 500, 502, 503, 504})


def credentials_available() -> bool:
    """Whether this process could reach the service at all."""
    return bool(os.environ.get(ENDPOINT_ENV) and os.environ.get(KEY_ENV))


# ---------------------------------------------------------------------------
# Mapping: service response -> kernel Document. No SDK, no network, no clock.
# ---------------------------------------------------------------------------


def _bbox_of(polygon: Sequence[float]) -> tuple[float, float, float, float]:
    """The axis-aligned bounds of a service polygon.

    The service returns four corners, which may be rotated for skewed text. The
    kernel stores axis-aligned boxes, so the enclosing rectangle is what is kept
    -- a slightly larger box that certainly contains the glyphs, rather than a
    tighter one that might not.
    """
    xs = [float(value) for value in polygon[0::2]]
    ys = [float(value) for value in polygon[1::2]]
    return min(xs), min(ys), max(xs), max(ys)


def _translated(error: Exception, source: SourceFile) -> ParserError:
    """Turn an unexpected failure into docdoc's error model.

    The response is someone else's data structure, and reading it can fail in
    ways this module did not anticipate: a renamed field, a null where a number
    belongs, a schema change shipped on a Tuesday. Those arrive as `KeyError`,
    `TypeError`, or a pydantic `ValidationError` — all of them provider-shaped
    failures crossing docdoc's public API, which the constitution's error model
    forbids outright.

    The detail is deliberately terse: the exception *type*, and for a missing key
    its name, which is part of the service's schema. The full message is dropped
    because a validation error can quote the offending value, and a value from a
    document is document content (FR-029).
    """
    detail = type(error).__name__
    if isinstance(error, KeyError) and error.args:
        detail = f"{detail}: missing field {error.args[0]!r}"
    return ParserError(
        "the service response could not be mapped to a document",
        reason="internal",
        parser_id=PARSER_ID,
        blob_id=source.blob_id,
        detail=detail,
    )


def map_analyze_result(
    result: Mapping[str, Any],
    *,
    source: SourceFile,
    options: Mapping[str, Any],
    text_layer: TextLayerRecord | None,
    parser_version: str = f"{ADAPTER_VERSION}+azure-di-{SERVICE_API_VERSION}",
) -> Document:
    """Turn one analyze result into a Document.

    Pure and offline, which is what lets the recorded-response tests exercise
    the real mapping without credentials (R14).

    Every failure leaves here as a docdoc error. That is the whole reason an
    adapter exists: the shape of the provider's data stops at this boundary
    (Principle IV, FR-025, ING-20).
    """
    try:
        return _map_analyze_result(
            result,
            source=source,
            options=options,
            text_layer=text_layer,
            parser_version=parser_version,
        )
    except DocdocError:
        raise  # already in the model, and more precise than anything added here
    except Exception as error:
        raise _translated(error, source) from error


def _map_analyze_result(
    result: Mapping[str, Any],
    *,
    source: SourceFile,
    options: Mapping[str, Any],
    text_layer: TextLayerRecord | None,
    parser_version: str,
) -> Document:
    text = str(result.get("content", ""))
    raw_pages = list(result.get("pages", ()))
    if not raw_pages and text:
        raise ParserError(
            "the service returned content but no pages",
            reason="empty_result",
            parser_id=PARSER_ID,
            blob_id=source.blob_id,
        )

    pages: list[Page] = []
    tokens: list[Token] = []
    geometry_by_page: dict[int, tuple[float, float]] = {}
    # The service numbers pages from 1 and, for a ranged analyze, need not start
    # at 1 or run contiguously. Tables reference pages by that number while
    # tokens are placed by position, so the two must be reconciled explicitly --
    # deriving each independently is how a table ends up anchored to a page its
    # own tokens never use.
    index_by_page_number: dict[int, int] = {}

    for index, raw in enumerate(raw_pages):
        width = float(raw.get("width") or 0.0)
        height = float(raw.get("height") or 0.0)
        geometry_by_page[index] = (width, height)
        if raw.get("pageNumber") is not None:
            index_by_page_number[int(raw["pageNumber"])] = index

        start = int(raw["spans"][0]["offset"]) if raw.get("spans") else 0
        # Pages must tile the text exactly, and the service leaves the newline
        # between pages outside both spans. Extending each page to where the
        # next begins closes the gap without inventing content.
        end = (
            int(raw_pages[index + 1]["spans"][0]["offset"])
            if index + 1 < len(raw_pages) and raw_pages[index + 1].get("spans")
            else len(text)
        )
        pages.append(
            Page(
                index=index,
                span=Span(start, end),
                width=width,
                height=height,
                # `angle` is estimated skew, not page rotation: the service
                # already reports coordinates as displayed.
                rotation=0,
            )
        )

        for word in raw.get("words", ()):
            span = word["span"]
            offset, length = int(span["offset"]), int(span["length"])
            tokens.append(
                Token(
                    span=Span(offset, offset + length),
                    geometry=Geometry(
                        page_index=index,
                        bbox=_normalize(
                            word["polygon"],
                            width=width,
                            height=height,
                            page_index=index,
                            source=source,
                        ),
                    ),
                    # Stored verbatim and not interpreted here: a service's own
                    # confidence is a passthrough, like a model's (ADR-0004).
                    source_confidence=(
                        float(word["confidence"]) if word.get("confidence") is not None else None
                    ),
                )
            )

    _check_ascending(tokens, source)

    return Document.create(
        text=text,
        pages=pages,
        tokens=tokens,
        tables=_map_tables(result, geometry_by_page, index_by_page_number, source),
        provenance=IngestProvenance(
            parser_id=PARSER_ID,
            parser_version=parser_version,
            options=dict(options),
            options_hash=options_fingerprint(options)[1],
            capabilities=AzureDocumentIntelligenceParser.capabilities.to_kernel(),
            text_layer_used=False,
            text_layer=text_layer,
            reading_order=AzureDocumentIntelligenceParser.reading_order,
        ),
        source=source.blob_ref(),
    )


def _normalize(
    polygon: Sequence[float],
    *,
    width: float,
    height: float,
    page_index: int,
    source: SourceFile,
) -> Any:
    x0, y0, x1, y1 = _bbox_of(polygon)
    try:
        return normalize_bbox(x0, y0, x1, y1, width=width, height=height, page_index=page_index)
    except GeometryError as error:
        raise ParserError(
            f"the service returned geometry outside page {page_index}",
            reason="internal",
            parser_id=PARSER_ID,
            blob_id=source.blob_id,
            detail=str(error),
        ) from error


def _check_ascending(tokens: Sequence[Token], source: SourceFile) -> None:
    """The service is expected to return words in reading order.

    If it does not, that is reported rather than repaired: sorting here would
    silently substitute docdoc's guess at reading order for the service's, and
    the declared ``reading_order`` would become a lie (FR-037).
    """
    for position in range(1, len(tokens)):
        if tokens[position].span.start < tokens[position - 1].span.end:
            raise ParserError(
                "the service returned words out of order or overlapping",
                reason="invalid_order",
                parser_id=PARSER_ID,
                blob_id=source.blob_id,
                detail=f"word {position} starts at {tokens[position].span.start}",
            )


def _resolve_page(
    region: Mapping[str, Any],
    index_by_page_number: Mapping[int, int],
    source: SourceFile,
) -> int:
    """Translate a service page number into this document's page index.

    Raises a ParserError naming the *actual* problem when the number is unknown.
    Falling back to ``pageNumber - 1`` would be right only when the response
    starts at page 1 and runs contiguously, and wrong -- silently, or with a
    misleading geometry error -- for a ranged analyze.
    """
    number = int(region["pageNumber"])
    try:
        return index_by_page_number[number]
    except KeyError:
        raise ParserError(
            f"the service placed a table on page {number}, which is not among the "
            f"pages it returned ({sorted(index_by_page_number)})",
            reason="internal",
            parser_id=PARSER_ID,
            blob_id=source.blob_id,
        ) from None


def _map_tables(
    result: Mapping[str, Any],
    geometry_by_page: Mapping[int, tuple[float, float]],
    index_by_page_number: Mapping[int, int],
    source: SourceFile,
) -> list[Table]:
    tables: list[Table] = []

    for position, raw in enumerate(result.get("tables", ())):
        regions = raw.get("boundingRegions") or ()
        if not regions or not raw.get("spans"):
            raise ParserError(
                f"the service returned table {position} with no text anchor",
                reason="internal",
                parser_id=PARSER_ID,
                blob_id=source.blob_id,
                detail="a table that cannot be located in the text is a half-fact",
            )
        page_index = _resolve_page(regions[0], index_by_page_number, source)
        width, height = geometry_by_page.get(page_index, (0.0, 0.0))
        span = raw["spans"][0]

        cells: list[TableCell] = []
        for cell in raw.get("cells", ()):
            if not cell.get("spans"):
                # Skipping it would leave a table still declaring its full
                # dimensions while quietly carrying one cell fewer -- a reader
                # would see a hole and have no way to learn it was ever filled.
                # Ordering and geometry are rejected rather than repaired for the
                # same reason (ING-8); a cell is no different.
                raise ParserError(
                    f"the service returned a cell at row {cell.get('rowIndex')}, "
                    f"column {cell.get('columnIndex')} of table {position} with no "
                    "text anchor",
                    reason="internal",
                    parser_id=PARSER_ID,
                    blob_id=source.blob_id,
                    detail=(
                        f"table declares {raw.get('rowCount')}x{raw.get('columnCount')}; "
                        "dropping the cell would understate it silently"
                    ),
                )
            cell_span = cell["spans"][0]
            cell_regions = cell.get("boundingRegions") or ()
            cells.append(
                TableCell(
                    span=Span(
                        int(cell_span["offset"]),
                        int(cell_span["offset"]) + int(cell_span["length"]),
                    ),
                    row=int(cell["rowIndex"]),
                    column=int(cell["columnIndex"]),
                    row_span=int(cell.get("rowSpan", 1)),
                    column_span=int(cell.get("columnSpan", 1)),
                    geometry=(
                        Geometry(
                            page_index=_resolve_page(cell_regions[0], index_by_page_number, source),
                            bbox=_normalize(
                                cell_regions[0]["polygon"],
                                width=width,
                                height=height,
                                page_index=page_index,
                                source=source,
                            ),
                        )
                        if cell_regions
                        else None
                    ),
                )
            )

        tables.append(
            Table(
                span=Span(int(span["offset"]), int(span["offset"]) + int(span["length"])),
                page_index=page_index,
                n_rows=int(raw["rowCount"]),
                n_columns=int(raw["columnCount"]),
                cells=tuple(cells),
                geometry=Geometry(
                    page_index=page_index,
                    bbox=_normalize(
                        regions[0]["polygon"],
                        width=width,
                        height=height,
                        page_index=page_index,
                        source=source,
                    ),
                ),
            )
        )

    return tables


# ---------------------------------------------------------------------------
# The parser itself
# ---------------------------------------------------------------------------


class AzureDocumentIntelligenceParser:
    """Sends a document to the service and maps what comes back."""

    id: Final = PARSER_ID
    version: Final = f"{ADAPTER_VERSION}+azure-di-{SERVICE_API_VERSION}"
    capabilities: Final = ParserCapabilities(
        text=True,
        geometry=True,
        tables=True,
        handwriting=True,
        media_types=frozenset({PDF, JPEG, PNG}),
        requires_network=True,
    )
    #: The service's own ordering. docdoc reconstructs nothing.
    reading_order: Final = "azure-di-service@1"

    def __init__(self, analyze: Any = None) -> None:
        #: Injectable so the retry and error-mapping behaviour is testable
        #: without a network. Production uses the SDK path below.
        self._analyze = analyze or self._analyze_over_the_wire

    def parse(
        self,
        source: SourceFile,
        options: Mapping[str, Any],
        transport: TransportSettings,
        text_layer: TextLayerRecord | None = None,
    ) -> Document:
        result = self._with_retries(source, transport)
        return map_analyze_result(
            result,
            source=source,
            options=options,
            text_layer=text_layer,
            parser_version=self.version,
        )

    # -- transport ---------------------------------------------------------

    def _with_retries(self, source: SourceFile, transport: TransportSettings) -> Mapping[str, Any]:
        """At most ``max_attempts`` tries, bounded by an overall deadline.

        Only transient failures are retried. A rejected credential or an
        unsupported document fails on the first attempt, because trying again
        cannot change the answer and doing so would just spend the deadline
        (ING-21).
        """
        deadline = transport.start()
        last: ProviderError | None = None

        for attempt in range(1, transport.max_attempts + 1):
            if deadline.expired:
                raise ProviderError(
                    "the overall deadline expired before the parse completed",
                    reason="deadline",
                    parser_id=self.id,
                    blob_id=source.blob_id,
                    attempts=attempt - 1,
                ) from last

            try:
                return self._analyze(source, transport, deadline)
            except ProviderError as error:
                error.attempts = attempt
                if not error.transient or attempt == transport.max_attempts:
                    raise
                last = error
                if not self._sleep_before_retry(attempt, transport, deadline, error):
                    raise ProviderError(
                        "the overall deadline left no room for another attempt",
                        reason="deadline",
                        parser_id=self.id,
                        blob_id=source.blob_id,
                        attempts=attempt,
                    ) from error

        raise ProviderError(  # pragma: no cover - loop always returns or raises
            "exhausted every attempt",
            reason="service",
            parser_id=self.id,
            blob_id=source.blob_id,
            attempts=transport.max_attempts,
        )

    def _sleep_before_retry(
        self,
        attempt: int,
        transport: TransportSettings,
        deadline: Deadline,
        error: ProviderError,
    ) -> bool:
        """Wait before the next attempt. False if the deadline forbids it.

        A service-supplied interval is a **floor**, not a suggestion. Jitter may
        extend it and must never shorten it: coming back early to a service that
        has just rate-limited you is how the next 429 is earned, and FR-038 says
        *honour* the interval, which 17 seconds is not when 30 were asked for.

        docdoc's own backoff is jittered in both directions, which is the usual
        defence against a fleet of clients retrying in lockstep. That reasoning
        does not transfer to an interval the server chose.

        A service that asks for longer than the budget allows does not get it:
        the parse fails on the deadline rather than sleeping past it.
        """
        requested = error.retry_after_s
        if requested is not None:
            wait = requested * (1.0 + random.random() * 0.25) if transport.jitter else requested
        else:
            wait = transport.backoff_for(attempt)
            if transport.jitter:
                wait *= 0.5 + random.random()

        if not deadline.allows(wait):
            return False
        time.sleep(wait)
        return True

    # -- the wire ----------------------------------------------------------

    def _analyze_over_the_wire(
        self, source: SourceFile, transport: TransportSettings, deadline: Deadline
    ) -> Mapping[str, Any]:
        """The real call. Every SDK type stays inside this method."""
        endpoint = os.environ.get(ENDPOINT_ENV)
        key = os.environ.get(KEY_ENV)
        if not endpoint or not key:
            raise ProviderError(
                f"the document-intelligence service is not configured; set {ENDPOINT_ENV} "
                f"and {KEY_ENV}",
                reason="auth",
                parser_id=self.id,
                blob_id=source.blob_id,
            )

        from azure.ai.documentintelligence import DocumentIntelligenceClient
        from azure.ai.documentintelligence.models import AnalyzeDocumentRequest
        from azure.core.credentials import AzureKeyCredential
        from azure.core.exceptions import (
            ClientAuthenticationError,
            HttpResponseError,
            ServiceRequestError,
        )

        client = DocumentIntelligenceClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(key),
            # docdoc owns the retry policy; a second one underneath would
            # multiply the attempt count past the documented bound (R12).
            retry_total=0,
        )
        try:
            poller = client.begin_analyze_document(
                "prebuilt-layout",
                AnalyzeDocumentRequest(bytes_source=source.data),
                string_index_type="unicodeCodePoint",
            )
            result = poller.result(timeout=min(transport.attempt_timeout_s, deadline.remaining_s))
        except ClientAuthenticationError as error:
            raise ProviderError(
                "the service rejected the credential",
                reason="auth",
                parser_id=self.id,
                blob_id=source.blob_id,
            ) from error
        except HttpResponseError as error:
            raise self._from_http(error, source) from error
        except ServiceRequestError as error:
            raise ProviderError(
                "the service could not be reached",
                reason="transport",
                parser_id=self.id,
                blob_id=source.blob_id,
            ) from error
        finally:
            client.close()

        return result.as_dict()

    def _from_http(
        self, error: Any, source: SourceFile
    ) -> ProviderError | UnsupportedDocumentError:
        """Translate one service failure, losing nothing a caller needs."""
        status = getattr(error, "status_code", None)
        if status in (400, 415):
            return UnsupportedDocumentError(
                "the service rejected the document as unreadable or unsupported",
                reason="corrupt",
                blob_id=source.blob_id,
                media_type=source.media_type,
                parser_id=self.id,
            )
        if status in (401, 403):
            return ProviderError(
                "the service rejected the credential",
                reason="auth",
                parser_id=self.id,
                blob_id=source.blob_id,
            )

        translated = ProviderError(
            f"the service returned {status or 'an error'}",
            reason="rate_limit"
            if status == 429
            else ("service" if status in _TRANSIENT_STATUS else "transport"),
            parser_id=self.id,
            blob_id=source.blob_id,
        )
        response = getattr(error, "response", None)
        retry_after = response.headers.get("Retry-After") if response is not None else None
        if retry_after:
            translated.retry_after_s = float(retry_after)
        return translated
