"""The PaddleX `/ocr` serving protocol, mapped to a Document — any number of
configured endpoints, one adapter.

ADR-0021 is why this module exists at all: `paddle` and `tesseract` are not
two providers each wanting their own file under `ingest/parsers/`, they are
one wire protocol — `POST /ocr`, `result.ocrResults[i].prunedResult` — spoken
by the official PaddleX serving CLI *and* by a hand-written shim that imitates
it. Coding a second adapter for the shim would be the hard-coded-provider-name
failure Principle IV exists to prevent, moved one layer down but not solved.
Instead this adapter is parametrised by configuration (`OcrEndpoint`,
`load_endpoints`): standing up a third PaddleX-protocol-compatible service, or
pointing an existing name at a new host, is a JSON edit, never a new adapter
file (ADR-0021 §3).

**This half is offline.** `pages_of` and `build_document` turn an already-
received response into a `Document` with no network, no clock, and no SDK —
the same split `gcv.map_annotate_result` draws between "the service answered"
and "here is what the answer means" (ADR-0021's whole adapter is testable
without the service it configures). The HTTP half — sending the request,
splitting a long PDF into endpoint-sized chunks, stitching the per-chunk page
lists this module hands back into one call to `build_document` — is a later
task's addition to this same module, not a different one.

**Line granularity, not words.** PaddleX recognizes *lines*, not words — there
is no word-level box to split a line into, and splitting on whitespace would
invent boundaries the service never reported. One `OcrLine` therefore becomes
one token-bearing line in the `Document` (the gateway design's own call,
"Line granularity"), via a single-element call to
`DocumentBuilder.add_line` per line — the same builder every other adapter
uses, so page spans tile and tokens stay ordered by construction rather than
by adapter discipline.

**Boxes travel in pixels until normalization, deliberately.** `OcrLine.box` is
`(x1, y1, x2, y2)` exactly as `rec_boxes` reports it — pixels of the *rendered*
page, which the adapter does not control and does not try to undo. Normalizing
only in `build_document`, against the page size `dataInfo` reports for that
same response, is what keeps a pixel coordinate from ever surviving into a
`Document` (FR-005) while leaving `pages_of` a pure, reusable extraction step
a future chunked caller can run once per chunk before any page size is known
to have survived intact.

**Bbox only, no polygons.** `rec_polys` is PaddleX-only and the kernel has no
polygon field; dropping it here is the gateway design's own call, not a gap
to close, and it is why `_pruned` in the fixture recorder never keeps it
either.

**A rotated page's boxes are not re-derived here, on purpose.** PaddleX's own
`doc_preprocessor_res` already corrects the image for display before OCR ever
runs on it — `rec_boxes` and `dataInfo`'s page size both describe the
*corrected* page, confirmed against a recorded `rotated_90.pdf` response where
`angle: 90` and every box still falls inside the reported (swapped) width and
height. So every page opens at `rotation=0`: there is no undisplayed rotation
left for this adapter to carry. If a box ever *did* fall outside the page —
evidence of a coordinate system this adapter does not understand, not
ordinary rendering slop — `normalize_bbox` raises `GeometryError` and it is
left to propagate unwrapped, unlike every `ParserError` this module raises
deliberately: a geometry bug is not a shape this protocol is expected to
produce, and folding it into `ParserError(reason="internal")` would make it
indistinguishable from one that is.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from docdoc.ingest.errors import ParserError, ProviderError
from docdoc.ingest.normalize import DocumentBuilder, normalize_bbox
from docdoc.ingest.options import options_fingerprint
from docdoc.ingest.source import JPEG, PDF, PNG
from docdoc.kernel import DocdocError, Document, IngestProvenance

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

    from docdoc.ingest.capabilities import ParserCapabilities
    from docdoc.ingest.source import SourceFile
    from docdoc.kernel import TextLayerRecord

__all__ = [
    "ADAPTER_VERSION",
    "ENDPOINTS_FILE_ENV",
    "READING_ORDER",
    "OcrEndpoint",
    "OcrLine",
    "OcrPage",
    "build_document",
    "load_endpoints",
    "pages_of",
]

ADAPTER_VERSION: Final = "1.0.0"

#: The order this adapter declares (FR-036, FR-037): the order lines arrive in
#: the service's own response, top of page to bottom, with no layout
#: reconstruction claimed on top. Every endpoint speaking this protocol gets
#: the same declaration, because the ordering it describes is the protocol's,
#: not any one deployment's.
READING_ORDER: Final = "paddlex-line-order@1"

#: Follows the `DOCDOC_*_FILE` convention `KeyRing.from_environment` and
#: `LimitBook.from_file` already use for a JSON file naming its own path
#: through the environment rather than through a CLI flag (ADR-0021 §3).
ENDPOINTS_FILE_ENV: Final = "DOCDOC_OCR_ENDPOINTS_FILE"

#: `ingest.registry.DEFAULT_PRIORITY`'s own three. A configured endpoint
#: reusing one of these ids would make `parser_id` ambiguous in every error
#: and every provenance record that names it, so `load_endpoints` refuses it
#: before it ever reaches the registry (ADR-0021 §3).
_BUILTIN_PARSER_IDS: Final = frozenset({"pdf-text", "azure-di", "gcv"})


class OcrEndpoint(BaseModel):
    """One configured PaddleX-protocol service: where it is, and what it is
    willing to be asked.

    ``url`` and ``headers`` carry ``repr=False``: this type is exactly what a
    discovery route or a log statement might print by accident, and neither a
    connection string nor a credential header belongs in either place (the
    gateway design's "Credential leakage" anti-goal; ADR-0021 §3's "no URL or
    header value appears in any error, log event, repr, or HTTP body").
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    #: The name every error and every provenance record uses instead of a URL
    #: (ADR-0021 §3). Lowercase-and-hyphens only, so it reads the same in a
    #: route path, a query parameter, and `parser_id` without quoting.
    id: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    url: str = Field(repr=False)
    #: What this deployment reports for this endpoint, carried into
    #: provenance alongside `ADAPTER_VERSION` — the same role
    #: `SERVICE_API_VERSION` plays for `gcv` and `azure_di`, except this one
    #: cannot be a module constant because a second endpoint can run a
    #: different build of the same protocol.
    version: str = Field(min_length=1)
    headers: dict[str, str] = Field(default_factory=dict, repr=False)
    media_types: frozenset[str] = frozenset({PDF, JPEG, PNG})
    languages: tuple[str, ...] = ()
    max_pages: int | None = Field(default=None, ge=1)

    @field_validator("url")
    @classmethod
    def _url_is_http(cls, value: str) -> str:
        if not (value.startswith("http://") or value.startswith("https://")):
            # The value itself is not echoed -- it is exactly the field this
            # type exists to keep out of a message (ADR-0021 §3).
            raise ValueError("endpoint url must start with http:// or https://")
        return value


def load_endpoints(path: str | Path) -> tuple[OcrEndpoint, ...]:
    """Read `{"endpoints": [...]}` from ``path``, refusing anything it cannot
    make sense of.

    Strict in the style `KeyRing.from_file` and `LimitBook.from_file` already
    are: a deployment that thinks it has a configured endpoint and does not
    should fail at startup, not on the first request that needed it.

    Raises:
        ValueError: the file cannot be read or parsed, an entry fails
            `OcrEndpoint`'s validation, two entries share an id, or an entry's
            id collides with a built-in parser. The message names the file and
            the entry's *index* only -- never a field's value, which for a bad
            entry may be exactly the url or header this type's `repr=False`
            exists to keep out of view.
    """
    path = Path(path)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except OSError as error:
        raise ValueError(f"{path} cannot be read: {type(error).__name__}") from error
    except json.JSONDecodeError as error:
        raise ValueError(f"{path} is not valid JSON") from error

    entries = raw.get("endpoints") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise ValueError(f"{path} must be an object with an `endpoints` array")

    seen_ids: set[str] = set()
    endpoints: list[OcrEndpoint] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"{path}: endpoints[{index}] is not an object")
        try:
            endpoint = OcrEndpoint(**entry)
        except ValidationError as error:
            # Deliberately not `str(error)`: pydantic's own message can quote
            # the offending value (a url, a header), which is exactly what
            # this function promises never to do.
            raise ValueError(
                f"{path}: endpoints[{index}] is not a valid endpoint "
                f"({len(error.errors())} field error(s))"
            ) from error

        # The id itself is not a secret -- it is the one thing every error in
        # this project *is* allowed to name an endpoint by -- so, unlike the
        # branch above, these two messages may say it.
        if endpoint.id in _BUILTIN_PARSER_IDS:
            raise ValueError(
                f"{path}: endpoints[{index}] uses id {endpoint.id!r}, which is a built-in "
                "parser id and cannot be reused by a configured endpoint"
            )
        if endpoint.id in seen_ids:
            raise ValueError(f"{path}: endpoints[{index}] reuses id {endpoint.id!r}")
        seen_ids.add(endpoint.id)
        endpoints.append(endpoint)

    return tuple(endpoints)


# ---------------------------------------------------------------------------
# Mapping: service response -> kernel Document. No SDK, no network, no clock.
# ---------------------------------------------------------------------------


class OcrLine(NamedTuple):
    """One recognized line: PaddleX's own granularity, never split into words."""

    text: str
    #: Pixels of the *rendered* page, `(x1, y1, x2, y2)` as `rec_boxes`
    #: reports it. Not yet normalized -- that needs the page size, which
    #: `build_document` has and `pages_of` deliberately does not keep around.
    box: tuple[float, float, float, float]
    #: The service's own confidence, carried through unmodified. `None` when
    #: the response carries no `rec_scores` at all (the Tesseract shim never
    #: does), which means "not reported", not "zero" (ADR-0004).
    score: float | None


class OcrPage(NamedTuple):
    """One page's lines, plus the page size they are relative to.

    A page with no lines is a real, representable value here -- a blank page
    in the middle of a scan is kept rather than dropped, so page indices
    downstream stay true to the pages that were actually sent (Review Focus
    2).
    """

    width: float
    height: float
    lines: tuple[OcrLine, ...]


def _translated(error: Exception, *, parser_id: str, blob_id: str | None) -> ParserError:
    """Turn an unexpected failure into docdoc's error model.

    Mirrors `gcv._translated`: the response is someone else's data structure,
    and the detail stays terse because a validation message can quote the
    offending value, and a value from a document is document content
    (FR-029).
    """
    detail = type(error).__name__
    if isinstance(error, KeyError) and error.args:
        detail = f"{detail}: missing field {error.args[0]!r}"
    return ParserError(
        "the service response could not be mapped to a document",
        reason="internal",
        parser_id=parser_id,
        blob_id=blob_id,
        detail=detail,
    )


def _raise_for_status(response: Mapping[str, Any], *, parser_id: str, blob_id: str | None) -> None:
    """Review Focus 4 — the old shim's failure style: a 200 whose body carries
    a nonzero `errorCode`. The accompanying `errorMsg` is the service's own
    prose (for the shim, literally an exception class name) and is dropped
    for the same reason every other provider's message is (FR-029).
    """
    code = response.get("errorCode")
    if code:
        raise ProviderError(
            f"the service reported errorCode {code}",
            reason="service",
            parser_id=parser_id,
            blob_id=blob_id,
        )


def _page_sizes(data_info: Mapping[str, Any]) -> list[tuple[float, float]]:
    """One `(width, height)` per page, from `dataInfo` alone.

    Two shapes, because the protocol reports page geometry differently for
    the two file types it accepts: a PDF carries one entry per page under
    `pages`; an image has exactly one page and reports its size at the top
    level. Anything else -- a renamed key, a type this adapter has never
    seen -- raises `KeyError` here and is wrapped into `ParserError` by the
    caller, rather than being guessed at.
    """
    if data_info.get("type") == "image":
        return [(float(data_info["width"]), float(data_info["height"]))]
    return [(float(page["width"]), float(page["height"])) for page in data_info["pages"]]


def _page_of(pruned: Mapping[str, Any], *, width: float, height: float) -> OcrPage:
    """One page's `prunedResult` -> one `OcrPage`.

    `rec_scores` is optional (the Tesseract shim never sends it); when absent
    every line's score is `None` rather than the lines being dropped or the
    page being refused. `zip(..., strict=True)` is the length check the task
    brief asks for: `rec_texts`, `rec_boxes`, and the per-line scores must
    agree, and a mismatch is a malformed response, not a silent truncation to
    the shortest list.
    """
    texts = pruned["rec_texts"]
    boxes = pruned["rec_boxes"]
    scores = pruned.get("rec_scores")
    if scores is None:
        scores = [None] * len(texts)

    lines = tuple(
        OcrLine(
            text=text, box=(float(box[0]), float(box[1]), float(box[2]), float(box[3])), score=score
        )
        for text, box, score in zip(texts, boxes, scores, strict=True)
    )
    return OcrPage(width=width, height=height, lines=lines)


def pages_of(response: Mapping[str, Any], *, parser_id: str, blob_id: str) -> list[OcrPage]:
    """One PaddleX-protocol `/ocr` response -> one page list.

    Pure and offline (R14): no field here is read from anywhere but the
    response itself, which is what lets the recorded fixtures exercise this
    without either live service running. A future chunked caller runs this
    once per chunk response and concatenates the results before the single
    call to `build_document` that produces one stitched `Document`.
    """
    try:
        return _pages_of(response, parser_id=parser_id, blob_id=blob_id)
    except DocdocError:
        raise  # already in the model, and more precise than anything added here
    except Exception as error:
        raise _translated(error, parser_id=parser_id, blob_id=blob_id) from error


def _pages_of(response: Mapping[str, Any], *, parser_id: str, blob_id: str) -> list[OcrPage]:
    _raise_for_status(response, parser_id=parser_id, blob_id=blob_id)

    result = response.get("result") or {}
    ocr_results = result.get("ocrResults") or ()
    sizes = _page_sizes(result.get("dataInfo") or {})

    return [
        _page_of(one["prunedResult"], width=width, height=height)
        for one, (width, height) in zip(ocr_results, sizes, strict=True)
    ]


def build_document(
    pages: Sequence[OcrPage],
    *,
    source: SourceFile,
    options: Mapping[str, Any],
    text_layer: TextLayerRecord | None,
    parser_id: str,
    parser_version: str,
    capabilities: ParserCapabilities,
) -> Document:
    """One or more `OcrPage`\\ s -> a Document.

    Takes an already-assembled page list rather than a raw response so a
    caller that split a long PDF into endpoint-sized chunks can concatenate
    several `pages_of` results before this runs once, over the whole
    document, with correct page indices throughout.
    """
    try:
        return _build_document(
            pages,
            source=source,
            options=options,
            text_layer=text_layer,
            parser_id=parser_id,
            parser_version=parser_version,
            capabilities=capabilities,
        )
    except DocdocError:
        raise
    except Exception as error:
        raise _translated(error, parser_id=parser_id, blob_id=source.blob_id) from error


def _build_document(
    pages: Sequence[OcrPage],
    *,
    source: SourceFile,
    options: Mapping[str, Any],
    text_layer: TextLayerRecord | None,
    parser_id: str,
    parser_version: str,
    capabilities: ParserCapabilities,
) -> Document:
    if not any(page.lines for page in pages):
        # Every page came back with nothing to read -- a blank middle page is
        # kept (Review Focus 2, below), but a document with no text anywhere
        # is reported rather than returned as a document that merely looks
        # successful.
        raise ParserError(
            "the service found no text on any page",
            reason="empty_result",
            parser_id=parser_id,
            blob_id=source.blob_id,
        )

    builder = DocumentBuilder(geometry=True)
    for index, page in enumerate(pages):
        # Every page opens, even a blank one (Review Focus 2) -- page indices
        # downstream must match the pages that were actually sent, and a
        # dropped page would shift every later index silently.
        builder.start_page(width=page.width, height=page.height, rotation=0)
        for line in page.lines:
            # Boxes already describe the corrected, as-displayed page (this
            # module's docstring); `normalize_bbox` either lands the box in
            # 0..1 or raises `GeometryError`, which is left to propagate
            # rather than becoming a `ParserError` (Review Focus 1).
            bbox = normalize_bbox(*line.box, width=page.width, height=page.height, page_index=index)
            builder.add_line([(line.text, bbox, line.score)])

    return builder.build(
        source=source.blob_ref(),
        provenance=IngestProvenance(
            parser_id=parser_id,
            parser_version=parser_version,
            options=dict(options),
            options_hash=options_fingerprint(options)[1],
            capabilities=capabilities.to_kernel(),
            text_layer_used=False,
            text_layer=text_layer,
            reading_order=READING_ORDER,
        ),
    )
