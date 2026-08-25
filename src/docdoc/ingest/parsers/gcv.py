"""Image OCR over Google Cloud Vision.

A second recognition path, for photographs and scanned images. Optional,
installed via ``docdoc[gcv]``.

**Images only, deliberately.** The service reads PDFs too, but only through
``asyncBatchAnnotate``, which requires the input *and* the output to sit in a
Cloud Storage bucket — a storage dependency, a polling loop, and a place for
document content to come to rest outside the process. The synchronous path this
adapter uses takes inline bytes and no bucket. PDFs keep the two parsers they
already have, and the media types declared here say so, so a PDF simply never
selects this adapter (FR-015).

**No tables.** This is OCR, not layout analysis: it returns words and boxes, not
cell structure. ``tables=False`` is therefore the honest declaration, and it is
why this adapter does *not* replace ``azure-di`` — a caller that asks for tables
will not be given this parser, which is the selection layer working correctly.

**Text is assembled here, not taken from the service.** The response carries a
``fullTextAnnotation.text`` string, and it is deliberately ignored. Unlike the
Azure path, the service supplies *no offsets* into it, so every token would have
to be located by searching that string — exactly the recovered-by-searching
correspondence that research.md R6 rejects. Building the text from the words
instead makes the token-to-text correspondence exact by construction, which is
what ``DocumentBuilder`` is for and what the native PDF path already does. The
cost is that the service's own line breaks are re-derived rather than adopted;
``_lines_of`` does that from the per-symbol break markers the service *does*
supply, so the layout is the service's, not a guess.

**Reading order** is declared ``gcv-block-order@1``: the order the service emits
blocks, then paragraphs, then words. No layout reconstruction is claimed on top
of it (FR-036, FR-037).
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, Final

from docdoc.ingest.capabilities import ParserCapabilities
from docdoc.ingest.errors import ParserError, ProviderError, UnsupportedDocumentError
from docdoc.ingest.normalize import DocumentBuilder, normalize_bbox
from docdoc.ingest.options import options_fingerprint
from docdoc.ingest.retry import analyze_with_retries
from docdoc.ingest.source import JPEG, PNG
from docdoc.kernel import BBox, DocdocError, Document, GeometryError, IngestProvenance

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from docdoc.ingest.options import Deadline, TransportSettings
    from docdoc.ingest.source import SourceFile
    from docdoc.kernel import TextLayerRecord

__all__ = ["GoogleCloudVisionParser", "credentials_available", "map_annotate_result"]

PARSER_ID: Final = "gcv"
ADAPTER_VERSION: Final = "1.0.0"
SERVICE_API_VERSION: Final = "v1"

#: Google's own variable, honoured because a deployment that already uses the
#: SDK has it set. Application Default Credentials can also come from `gcloud
#: auth` or a metadata server, neither of which is visible as an environment
#: variable -- a deployment on those sets DOCDOC_GCV_CREDENTIALS to any non-empty
#: value to say so. Guessing "credentials probably exist" would turn a missing
#: configuration into a failed parse instead of an unavailable parser, and the
#: registry's whole point is telling those two apart (FR-018).
GOOGLE_CREDENTIALS_ENV: Final = "GOOGLE_APPLICATION_CREDENTIALS"
CREDENTIALS_ENV: Final = "DOCDOC_GCV_CREDENTIALS"

#: Break markers that end a line. The rest (`SPACE`, `SURE_SPACE`, `HYPHEN`)
#: fall within one. `HYPHEN` is *not* treated as a line end and the hyphen is not
#: rejoined: dehyphenation is a downstream decision, not an adapter's (FR-007).
_LINE_ENDING_BREAKS: Final = frozenset({"EOL_SURE_SPACE", "LINE_BREAK"})


def credentials_available() -> bool:
    """Whether this process could reach the service at all."""
    return bool(os.environ.get(GOOGLE_CREDENTIALS_ENV) or os.environ.get(CREDENTIALS_ENV))


# ---------------------------------------------------------------------------
# Mapping: service response -> kernel Document. No SDK, no network, no clock.
# ---------------------------------------------------------------------------


def _translated(error: Exception, source: SourceFile) -> ParserError:
    """Turn an unexpected failure into docdoc's error model.

    The same reasoning as the Azure adapter's: the response is someone else's
    data structure, and the detail is kept terse because a validation message can
    quote the offending value, and a value from a document is document content
    (FR-029).
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


def map_annotate_result(
    result: Mapping[str, Any],
    *,
    source: SourceFile,
    options: Mapping[str, Any],
    text_layer: TextLayerRecord | None,
    parser_version: str = f"{ADAPTER_VERSION}+gcv-{SERVICE_API_VERSION}",
) -> Document:
    """Turn one ``AnnotateImageResponse`` into a Document.

    Pure and offline, which is what lets the recorded-response tests exercise the
    real mapping without credentials (R14).

    The mapping expects the REST JSON shape -- camelCase keys, enums as names --
    which is what ``_annotate_over_the_wire`` converts the SDK's response into.
    Pinning one shape here means a recorded fixture is a plain API response and
    the mapping has no branch that only the SDK path ever takes.
    """
    try:
        return _map_annotate_result(
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


def _map_annotate_result(
    result: Mapping[str, Any],
    *,
    source: SourceFile,
    options: Mapping[str, Any],
    text_layer: TextLayerRecord | None,
    parser_version: str,
) -> Document:
    _raise_for_status(result, source)

    annotation = result.get("fullTextAnnotation")
    if not annotation:
        # A blank image is a real outcome, but the response carries no page
        # dimensions when it finds no text, so there is no page to report and no
        # geometry any downstream question could be answered against. An explicit
        # refusal beats a zero-page document that looks like a successful parse.
        raise ParserError(
            "the service found no text and returned no page dimensions",
            reason="empty_result",
            parser_id=PARSER_ID,
            blob_id=source.blob_id,
        )

    raw_pages = list(annotation.get("pages") or ())
    if not raw_pages:
        raise ParserError(
            "the service returned a text annotation but no pages",
            reason="empty_result",
            parser_id=PARSER_ID,
            blob_id=source.blob_id,
        )

    builder = DocumentBuilder(geometry=True)
    words_seen = 0
    for index, raw in enumerate(raw_pages):
        width = float(raw.get("width") or 0.0)
        height = float(raw.get("height") or 0.0)
        # The service reports coordinates as displayed, so there is no rotation
        # to undo here -- unlike the native PDF path, where word boxes arrive in
        # unrotated space.
        builder.start_page(width=width, height=height, rotation=0)

        for line in _lines_of(raw):
            words_seen += len(line)
            builder.add_line(
                [
                    (
                        _text_of(word),
                        _box_of(word, width=width, height=height, page_index=index, source=source),
                        _confidence_of(word),
                    )
                    for word in line
                ]
            )

    if not words_seen:
        # The service sent a text annotation with pages but nothing readable in
        # them -- a shape it has no reason to produce, so it means the walk down
        # to the words found a key it did not recognize. Building anyway would
        # hand back a document with pages, no text, and no way for a caller to
        # tell that from an image that genuinely had nothing on it.
        raise ParserError(
            "the service returned pages containing no words",
            reason="empty_result",
            parser_id=PARSER_ID,
            blob_id=source.blob_id,
        )

    return builder.build(
        source=source.blob_ref(),
        provenance=IngestProvenance(
            parser_id=PARSER_ID,
            parser_version=parser_version,
            options=dict(options),
            options_hash=options_fingerprint(options)[1],
            capabilities=GoogleCloudVisionParser.capabilities.to_kernel(),
            text_layer_used=False,
            text_layer=text_layer,
            reading_order=GoogleCloudVisionParser.reading_order,
        ),
    )


def _raise_for_status(result: Mapping[str, Any], source: SourceFile) -> None:
    """A per-image error travels *inside* a 200 response, not as an exception.

    Only the numeric status code is carried over. The accompanying message is
    the service's prose and can quote the request, so it is dropped for the same
    reason the mapping errors drop theirs (FR-029).
    """
    status = result.get("error")
    if not status:
        return

    code = int(status.get("code") or 0)
    # google.rpc.Code 3 INVALID_ARGUMENT, 9 FAILED_PRECONDITION: the image itself
    # is the problem, and no retry or credential changes that.
    if code in (3, 9):
        raise UnsupportedDocumentError(
            "the service rejected the image as unreadable or unsupported",
            reason="corrupt",
            blob_id=source.blob_id,
            media_type=source.media_type,
            parser_id=PARSER_ID,
        )
    if code in (7, 16):  # PERMISSION_DENIED, UNAUTHENTICATED
        raise ProviderError(
            "the service rejected the credential",
            reason="auth",
            parser_id=PARSER_ID,
            blob_id=source.blob_id,
        )
    raise ProviderError(
        f"the service reported status {code} for the image",
        reason="rate_limit" if code == 8 else "service",  # 8 RESOURCE_EXHAUSTED
        parser_id=PARSER_ID,
        blob_id=source.blob_id,
    )


def _lines_of(page: Mapping[str, Any]) -> list[list[Mapping[str, Any]]]:
    """Walk block -> paragraph -> word, splitting where the service says a line ends.

    The service structures a page as blocks of paragraphs, not as lines, and a
    paragraph routinely wraps. The line boundary it *does* report is the
    ``detectedBreak`` on a word's last symbol, so that is what is read here.

    A paragraph always ends a line even without a break marker: two paragraphs
    running together would join text the service showed as separate, and the
    builder's line break is the only separator the assembled text has.
    """
    lines: list[list[Mapping[str, Any]]] = []

    for block in page.get("blocks") or ():
        for paragraph in block.get("paragraphs") or ():
            current: list[Mapping[str, Any]] = []
            for word in paragraph.get("words") or ():
                current.append(word)
                if _ends_line(word):
                    lines.append(current)
                    current = []
            if current:
                lines.append(current)

    return lines


def _ends_line(word: Mapping[str, Any]) -> bool:
    """Whether the service marked a line break after this word."""
    symbols = word.get("symbols") or ()
    if not symbols:
        return False
    detected = (symbols[-1].get("property") or {}).get("detectedBreak") or {}
    return str(detected.get("type") or "") in _LINE_ENDING_BREAKS


def _text_of(word: Mapping[str, Any]) -> str:
    """A word's text, concatenated from its symbols.

    The service reports no word-level text -- only symbols -- so this is
    concatenation, not normalization: nothing is stripped, substituted, or
    case-folded (FR-007).
    """
    return "".join(str(symbol.get("text") or "") for symbol in word.get("symbols") or ())


def _confidence_of(word: Mapping[str, Any]) -> float | None:
    """The service's own confidence, carried through and never interpreted here."""
    confidence = word.get("confidence")
    return float(confidence) if confidence is not None else None


def _box_of(
    word: Mapping[str, Any],
    *,
    width: float,
    height: float,
    page_index: int,
    source: SourceFile,
) -> BBox:
    """The word's box, normalized to the kernel's 0..1 top-left space.

    Two vertex forms exist. ``vertices`` are pixels and need the page dimensions;
    ``normalizedVertices`` are already 0..1 and must *not* be divided again. The
    service picks per request type, so both are handled rather than assumed.
    """
    box = word.get("boundingBox") or {}
    vertices = box.get("vertices")
    if vertices:
        scale_w, scale_h = width, height
    else:
        vertices = box.get("normalizedVertices")
        # Already 0..1: dividing by 1 leaves them alone while still running them
        # through the same out-of-page check.
        scale_w, scale_h = 1.0, 1.0

    if not vertices:
        # Geometry is all-or-nothing (ING-4), and this parser declares it. One
        # word without a box makes the whole document's declaration false, so it
        # is reported rather than emitted as a token with no geometry.
        raise ParserError(
            f"the service returned a word with no bounding box on page {page_index}",
            reason="internal",
            parser_id=PARSER_ID,
            blob_id=source.blob_id,
        )

    x0, y0, x1, y1 = _bounds_of(vertices)
    try:
        return normalize_bbox(x0, y0, x1, y1, width=scale_w, height=scale_h, page_index=page_index)
    except GeometryError as error:
        raise ParserError(
            f"the service returned geometry outside page {page_index}",
            reason="internal",
            parser_id=PARSER_ID,
            blob_id=source.blob_id,
            detail=str(error),
        ) from error


def _bounds_of(vertices: Sequence[Mapping[str, Any]]) -> tuple[float, float, float, float]:
    """The axis-aligned bounds of the service's four corners.

    The corners may be rotated for skewed text while the kernel stores
    axis-aligned boxes, so the enclosing rectangle is what is kept.

    A zero coordinate is *absent* from the JSON rather than present as ``0`` --
    a protobuf default that does not survive the round trip. Defaulting to 0.0 is
    therefore reading the format correctly, not papering over a gap: a word
    touching the left or top edge is exactly the case that omits it.
    """
    xs = [float(vertex.get("x") or 0.0) for vertex in vertices]
    ys = [float(vertex.get("y") or 0.0) for vertex in vertices]
    return min(xs), min(ys), max(xs), max(ys)


# ---------------------------------------------------------------------------
# The parser itself
# ---------------------------------------------------------------------------


class GoogleCloudVisionParser:
    """Sends an image to the service and maps what comes back."""

    id: Final = PARSER_ID
    version: Final = f"{ADAPTER_VERSION}+gcv-{SERVICE_API_VERSION}"
    capabilities: Final = ParserCapabilities(
        text=True,
        geometry=True,
        # OCR, not layout analysis: no cell structure comes back to map.
        tables=False,
        handwriting=True,
        media_types=frozenset({JPEG, PNG}),
        requires_network=True,
    )
    #: The service's own ordering. docdoc reconstructs nothing.
    reading_order: Final = "gcv-block-order@1"

    def __init__(self, annotate: Any = None) -> None:
        #: Injectable so the retry and error-mapping behaviour is testable
        #: without a network. Production uses the SDK path below.
        self._annotate = annotate or self._annotate_over_the_wire

    def parse(
        self,
        source: SourceFile,
        options: Mapping[str, Any],
        transport: TransportSettings,
        text_layer: TextLayerRecord | None = None,
    ) -> Document:
        result = analyze_with_retries(
            self._annotate, source=source, transport=transport, parser_id=self.id
        )
        return map_annotate_result(
            result,
            source=source,
            options=options,
            text_layer=text_layer,
            parser_version=self.version,
        )

    # -- the wire ----------------------------------------------------------

    def _annotate_over_the_wire(
        self, source: SourceFile, transport: TransportSettings, deadline: Deadline
    ) -> Mapping[str, Any]:
        """The real call. Every SDK type stays inside this method."""
        if not credentials_available():
            raise ProviderError(
                f"the vision service is not configured; set {GOOGLE_CREDENTIALS_ENV}, or "
                f"{CREDENTIALS_ENV} if this deployment supplies credentials another way",
                reason="auth",
                parser_id=self.id,
                blob_id=source.blob_id,
            )

        from google.api_core import exceptions as api_exceptions
        from google.auth import exceptions as auth_exceptions
        from google.cloud import vision

        client = vision.ImageAnnotatorClient()
        try:
            response = client.annotate_image(
                {
                    "image": {"content": source.data},
                    # DOCUMENT_TEXT_DETECTION, not TEXT_DETECTION: the dense-text
                    # model is the one that returns the block/paragraph/word tree
                    # and the break markers this adapter reads.
                    "features": [{"type_": vision.Feature.Type.DOCUMENT_TEXT_DETECTION}],
                },
                # docdoc owns the retry policy; a second one underneath would
                # multiply the attempt count past the documented bound (R12).
                retry=None,
                timeout=min(transport.attempt_timeout_s, max(deadline.remaining_s, 0.0)),
            )
        except auth_exceptions.GoogleAuthError as error:
            raise ProviderError(
                "the credential could not be loaded or was rejected",
                reason="auth",
                parser_id=self.id,
                blob_id=source.blob_id,
            ) from error
        except api_exceptions.GoogleAPICallError as error:
            raise self._from_api_error(error, source) from error
        except api_exceptions.RetryError as error:
            raise ProviderError(
                "the service could not be reached",
                reason="transport",
                parser_id=self.id,
                blob_id=source.blob_id,
            ) from error

        # camelCase keys and named enums, so the mapping sees the documented REST
        # shape rather than the SDK's proto field names. Annotated on the way out
        # because the SDK is untyped: this is the line where the untyped world
        # stops (Principle IV).
        converted: dict[str, Any] = vision.AnnotateImageResponse.to_dict(
            response,
            preserving_proto_field_name=False,
            use_integers_for_enums=False,
        )
        return converted

    def _from_api_error(
        self, error: Any, source: SourceFile
    ) -> ProviderError | UnsupportedDocumentError:
        """Translate one service failure, losing nothing a caller needs."""
        status = getattr(error, "code", None)
        status = int(status) if isinstance(status, int) else None

        if status in (400, 415):
            return UnsupportedDocumentError(
                "the service rejected the image as unreadable or unsupported",
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
            reason=self._reason_for(status),
            parser_id=self.id,
            blob_id=source.blob_id,
        )
        response = getattr(error, "response", None)
        headers = getattr(response, "headers", None)
        retry_after = headers.get("Retry-After") if headers is not None else None
        if retry_after:
            translated.retry_after_s = float(retry_after)
        return translated

    @staticmethod
    def _reason_for(status: int | None) -> str:
        if status == 429:
            return "rate_limit"
        if status == 408 or status == 504:
            return "timeout"
        if status in (500, 502, 503):
            return "service"
        return "transport"
