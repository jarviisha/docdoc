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
`(x1, y1, x2, y2)` exactly as `rec_boxes` reports it — pixels of whatever frame
OCR actually ran against (see `_oriented_size` below for when that differs
from the page size `dataInfo` names). `OcrPage.width`/`height` already carry
that corrected size, so `build_document`'s call to `normalize_bbox` is the
only place a pixel coordinate is divided down to the kernel's 0..1 space; it
never survives into a `Document` (FR-005), and `pages_of` stays a pure,
reusable extraction step a future chunked caller can run once per chunk.

**Bbox only, no polygons.** `rec_polys` is PaddleX-only and the kernel has no
polygon field; dropping it here is the gateway design's own call, not a gap
to close, and it is why `_pruned` in the fixture recorder never keeps it
either.

**A rotated page's boxes are re-derived here, deliberately not against
`dataInfo` alone.** PaddleX's own `doc_preprocessor_res` corrects the image
for display *before* OCR ever runs on it, so `rec_boxes` describes the
corrected frame — but `dataInfo` keeps reporting the page **as rendered**,
before that correction. For a quarter turn (`angle` 90 or 270) those two
frames have width and height swapped, confirmed against a recorded
`rotated_90.pdf` response: `dataInfo` says `1684x1190` with `angle: 90`, and
the first box is barely different from the *unrotated* `digital_invoice.pdf`
fixture's own first box once width and height are swapped back. Dividing by
the unswapped size instead does not crash — the result still happens to fall
inside `[0, 1]` — it is simply the wrong rectangle, silently. `_oriented_size`
is where this swap happens, once, before either `normalize_bbox` or the
`Document`'s own page dimensions see a size. Every page still opens at
`rotation=0`: once the frame is right, there is no further undisplayed
rotation left for this adapter to carry. If a box still falls outside the
(now correctly oriented) page — evidence of a coordinate system this adapter
does not understand, not ordinary rendering slop — `normalize_bbox` raises
`GeometryError` and it is left to propagate unwrapped, unlike every
`ParserError` this module raises deliberately: a geometry bug is not a shape
this protocol is expected to produce, and folding it into
`ParserError(reason="internal")` would make it indistinguishable from one
that is.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final, NamedTuple

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from docdoc.ingest.capabilities import ParserCapabilities
from docdoc.ingest.errors import (
    ParserCapabilityError,
    ParserError,
    ProviderError,
    UnsupportedDocumentError,
)
from docdoc.ingest.normalize import DocumentBuilder, normalize_bbox
from docdoc.ingest.options import options_fingerprint
from docdoc.ingest.retry import analyze_with_retries
from docdoc.ingest.source import JPEG, PDF, PNG
from docdoc.kernel import DocdocError, Document, IngestProvenance

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from docdoc.ingest.options import Deadline, TransportSettings
    from docdoc.ingest.source import SourceFile
    from docdoc.kernel import TextLayerRecord

__all__ = [
    "ADAPTER_VERSION",
    "ENDPOINTS_FILE_ENV",
    "READING_ORDER",
    "OcrEndpoint",
    "OcrLine",
    "OcrPage",
    "PaddleXParser",
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
            # Deliberately not `str(error)`, and deliberately `from None`
            # rather than `from error`: pydantic's own message -- and its
            # `errors()[i]["input"]` -- can carry the offending value (a url,
            # a header). Chaining with `from error` would still attach it as
            # `__cause__`, which `traceback.format_exception` walks and
            # prints in full; `from None` is what actually keeps it out of
            # view, not just out of this function's own message.
            raise ValueError(
                f"{path}: endpoints[{index}] is not a valid endpoint "
                f"({len(error.errors())} field error(s))"
            ) from None

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
    """One `(width, height)` per page, from `dataInfo` alone -- the size of
    the page **as rendered**, before `doc_preprocessor_res` corrects it.

    Two shapes, because the protocol reports page geometry differently for
    the two file types it accepts: a PDF carries one entry per page under
    `pages`; an image has exactly one page and reports its size at the top
    level. Anything else -- a renamed key, a type this adapter has never
    seen -- raises `KeyError` here and is wrapped into `ParserError` by the
    caller, rather than being guessed at.

    This is deliberately *not* the frame `rec_boxes` is relative to when the
    page was rotated a quarter turn -- see `_oriented_size`, which every
    caller of this function must apply before trusting either number.
    """
    if data_info.get("type") == "image":
        return [(float(data_info["width"]), float(data_info["height"]))]
    return [(float(page["width"]), float(page["height"])) for page in data_info["pages"]]


#: The angles `doc_preprocessor_res.angle` reports that swap which of a
#: page's two reported dimensions is the width. The classifier only ever
#: reports a right angle (0, 90, 180, 270); a half turn leaves width and
#: height as they were, so only these two matter here.
_SWAPPED_ANGLES = (90, 270)


def _oriented_size(size: tuple[float, float], pruned: Mapping[str, Any]) -> tuple[float, float]:
    """The page size `rec_boxes` is actually relative to -- not always the
    one `dataInfo` reports.

    `dataInfo` names the page **as rendered**, before PaddleX's own
    `doc_preprocessor_res` straightens it for display. When that correction
    is a quarter turn, OCR runs on the *straightened* image and `rec_boxes`
    describes that image's frame, which has its width and height swapped
    from what `dataInfo` reported for the as-rendered one.

    Confirmed against a recorded `rotated_90.pdf` response: `dataInfo`
    reports `1684x1190` with `angle: 90`, and the first box
    (`[119, 142, 389, 162]`) is barely different from the *unrotated*
    `digital_invoice.pdf` fixture's own first box (`[119, 141, 389, 161]`,
    against a reported `1190x1684`) -- the same invoice, recognized in
    nearly the same place, once width and height are swapped back. Dividing
    that box by the unswapped `1684x1190` instead lands it in the wrong
    aspect ratio silently: still inside `[0, 1]` (so `normalize_bbox` never
    objects), just describing the wrong rectangle.

    The Tesseract shim never reports `doc_preprocessor_res` at all, so its
    pages are never swapped here -- consistent with it performing no
    orientation correction of its own.
    """
    angle = (pruned.get("doc_preprocessor_res") or {}).get("angle")
    if angle in _SWAPPED_ANGLES:
        width, height = size
        return height, width
    return size


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
    rendered_sizes = _page_sizes(result.get("dataInfo") or {})
    pruned_results = [one["prunedResult"] for one in ocr_results]

    pages = []
    for pruned, rendered_size in zip(pruned_results, rendered_sizes, strict=True):
        width, height = _oriented_size(rendered_size, pruned)
        pages.append(_page_of(pruned, width=width, height=height))
    return pages


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


# ---------------------------------------------------------------------------
# The wire: sending a request, splitting a long PDF, stitching the results.
# ---------------------------------------------------------------------------

#: PaddleX's own ``fileType`` codes (record.py), named rather than inlined.
_FILE_TYPE_PDF: Final = 0
_FILE_TYPE_IMAGE: Final = 1


def _pdfium_installed() -> bool:
    """Whether ``pypdfium2`` (the ``ocr`` extra) is importable.

    Mirrors ``registry._sdk_installed``: a probe via `find_spec`, not a bare
    `import` wrapped in `except ImportError`, which `_pdf_chunks` below can
    monkeypatch in a test without fighting Python's import machinery.
    """
    try:
        return importlib.util.find_spec("pypdfium2") is not None
    except (ImportError, ValueError):
        return False


def _post_over_the_wire(
    endpoint: OcrEndpoint, body: dict[str, Any], timeout_s: float
) -> Mapping[str, Any]:
    """The real call. ``urllib`` only -- the one place this module uses it,
    and the reason ``post`` is an injected parameter rather than a fixed
    implementation: every other test replaces it and never opens a socket.

    Every failure below is translated before it leaves this function, with
    ``from None`` throughout: `urllib.error.HTTPError` and `URLError` embed
    the request URL in their own ``str()``, and chaining them as `__cause__`
    would smuggle it back in behind this adapter's own message (Review
    Focus 3) -- `from None` is what actually drops it, not just omits it from
    the message written here.
    """
    request = urllib.request.Request(
        endpoint.url,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", **endpoint.headers},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            raw = response.read()
    except urllib.error.HTTPError as error:
        raise _from_http_error(error, endpoint) from None
    except urllib.error.URLError:
        raise ProviderError(
            f"endpoint {endpoint.id!r} could not be reached",
            reason="transport",
            parser_id=endpoint.id,
        ) from None

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        raise ProviderError(
            f"endpoint {endpoint.id!r} returned a response that was not valid JSON",
            reason="service",
            parser_id=endpoint.id,
        ) from None


def _from_http_error(
    error: urllib.error.HTTPError, endpoint: OcrEndpoint
) -> ProviderError | UnsupportedDocumentError:
    """Translate one HTTP status into docdoc's model. No URL, no header, no body."""
    status = error.code
    if status in (401, 403):
        return ProviderError(
            f"endpoint {endpoint.id!r} rejected the credential",
            reason="auth",
            parser_id=endpoint.id,
        )
    if status in (400, 415, 422):
        return UnsupportedDocumentError(
            f"endpoint {endpoint.id!r} rejected the file as unreadable or unsupported",
            reason="corrupt",
            parser_id=endpoint.id,
        )

    reason = "rate_limit" if status == 429 else ("timeout" if status == 504 else "service")
    translated = ProviderError(
        f"endpoint {endpoint.id!r} returned {status}",
        reason=reason,
        parser_id=endpoint.id,
    )
    retry_after = error.headers.get("Retry-After") if error.headers is not None else None
    if retry_after:
        translated.retry_after_s = float(retry_after)
    return translated


class PaddleXParser:
    """Sends a document to one configured PaddleX-protocol endpoint and maps
    what comes back.

    One instance per `OcrEndpoint` -- unlike `AzureDocumentIntelligenceParser`
    or `GoogleCloudVisionParser`, this adapter is parametrised by
    configuration rather than fixed at class definition (ADR-0021 §3), so
    `id`, `version`, `capabilities`, and `languages` are computed once per
    instance, not declared once per class.
    """

    def __init__(
        self,
        endpoint: OcrEndpoint,
        post: Callable[[OcrEndpoint, dict[str, Any], float], Mapping[str, Any]] | None = None,
    ) -> None:
        self._endpoint = endpoint
        #: Injectable so the splitting, retry, and error-mapping behaviour is
        #: testable with no socket. Production uses `_post_over_the_wire`.
        self._post = post or _post_over_the_wire

        self.id = endpoint.id
        #: The adapter's own version plus this *deployment's* protocol
        #: version -- not a module constant, because a second endpoint can
        #: run a different build of the same protocol (`OcrEndpoint.version`).
        self.version = f"{ADAPTER_VERSION}+{endpoint.version}"
        self.capabilities = ParserCapabilities(
            text=True,
            geometry=True,
            tables=False,
            handwriting=False,
            media_types=endpoint.media_types,
            requires_network=True,
        )
        #: The protocol's own ordering. Every endpoint speaking it declares
        #: the same one -- see `READING_ORDER`'s own docstring.
        self.reading_order = READING_ORDER
        self.languages = endpoint.languages

    def parse(
        self,
        source: SourceFile,
        options: Mapping[str, Any],
        transport: TransportSettings,
        text_layer: TextLayerRecord | None = None,
    ) -> Document:
        self._check_options(options, blob_id=source.blob_id)

        if source.is_pdf:
            pages = self._parse_pdf(source, transport)
        else:
            pages = analyze_with_retries(
                lambda src, tr, dl: self._send(source.data, _FILE_TYPE_IMAGE, 1, src, tr, dl),
                source=source,
                transport=transport,
                parser_id=self.id,
            )

        return build_document(
            pages,
            source=source,
            options=options,
            text_layer=text_layer,
            parser_id=self.id,
            parser_version=self.version,
            capabilities=self.capabilities,
        )

    def _check_options(self, options: Mapping[str, Any], *, blob_id: str) -> None:
        """Option checks first, and before any network call (ADR-0021 §3):
        the only recognized key is ``language``, and only a value the
        endpoint actually declared.
        """
        for key, value in options.items():
            if key != "language":
                raise ParserCapabilityError(
                    f"endpoint {self.id!r} does not accept option {key!r}; only "
                    "'language' is supported",
                    required=(f"option={key}",),
                    candidates=((self.id, True, None),),
                    blob_id=blob_id,
                )
            if value not in self._endpoint.languages:
                raise ParserCapabilityError(
                    f"endpoint {self.id!r} does not support language {value!r}",
                    required=(f"language={value}",),
                    candidates=((self.id, True, None),),
                    blob_id=blob_id,
                )

    def _parse_pdf(self, source: SourceFile, transport: TransportSettings) -> list[OcrPage]:
        pages: list[OcrPage] = []
        for data, expected_pages in self._pdf_chunks(source):
            chunk_pages = analyze_with_retries(
                lambda src, tr, dl, data=data, expected_pages=expected_pages: self._send(
                    data, _FILE_TYPE_PDF, expected_pages, src, tr, dl
                ),
                source=source,
                transport=transport,
                parser_id=self.id,
            )
            pages.extend(chunk_pages)
        return pages

    def _pdf_chunks(self, source: SourceFile) -> list[tuple[bytes, int]]:
        """Open the PDF once, learn its page count, and cut it into
        ``endpoint.max_pages``-sized sub-PDFs if it is longer than that.

        Every failure here is a typed docdoc error, raised before any network
        call (Review Focus 5): the file either cannot be read at all, or this
        process cannot read it without an extra it does not have installed.

        **Deviation from the original design, recorded here and in the
        design doc:** the plan assumed a PDF within the endpoint's limit
        would still parse without `docdoc[ocr]`. It cannot: without a PDF
        reader there is no way to learn the page count, and without the page
        count this adapter cannot tell a complete result from PaddleX's own
        silent truncation (record.py) -- the one defence ING-7 rests on. An
        image needs none of this and is unaffected.
        """
        if not _pdfium_installed():
            raise ParserCapabilityError(
                f"endpoint {self.id!r} needs docdoc[ocr] (pypdfium2) to send a PDF",
                required=("media_type=application/pdf",),
                media_type=source.media_type,
                candidates=((self.id, False, "extra_not_installed"),),
                blob_id=source.blob_id,
            )

        import pypdfium2 as pdfium

        try:
            document = pdfium.PdfDocument(source.data)
        except pdfium.PdfiumError as error:
            reason = "encrypted" if error.err_code == pdfium.raw.FPDF_ERR_PASSWORD else "corrupt"
            raise UnsupportedDocumentError(
                f"endpoint {self.id!r} was sent a PDF that could not be opened",
                reason=reason,
                blob_id=source.blob_id,
                media_type=source.media_type,
                parser_id=self.id,
            ) from None

        try:
            page_count = len(document)
            max_pages = self._endpoint.max_pages
            if max_pages is None or page_count <= max_pages:
                # Sent exactly as received -- no re-encoding through
                # pypdfium2 when no splitting is needed (record.py: exactly
                # at the limit is fine; the limit is `>`, not `>=`).
                return [(source.data, page_count)]

            chunks: list[tuple[bytes, int]] = []
            for start in range(0, page_count, max_pages):
                count = min(max_pages, page_count - start)
                chunk_doc = pdfium.PdfDocument.new()
                try:
                    chunk_doc.import_pages(document, pages=list(range(start, start + count)))
                    buffer = io.BytesIO()
                    chunk_doc.save(buffer)
                finally:
                    chunk_doc.close()
                chunks.append((buffer.getvalue(), count))
            return chunks
        finally:
            document.close()

    def _send(
        self,
        data: bytes,
        file_type: int,
        expected_pages: int,
        source: SourceFile,
        transport: TransportSettings,
        deadline: Deadline,
    ) -> list[OcrPage]:
        """One request, one chunk. Raises `ProviderError` on anything
        `analyze_with_retries` should consider retrying -- including a short
        result, which is PaddleX's own silent-truncation failure mode and
        the one thing only this caller can detect (record.py, ING-7).
        """
        timeout_s = min(transport.attempt_timeout_s, deadline.remaining_s)
        body = {"file": base64.b64encode(data).decode("ascii"), "fileType": file_type}
        response = self._post(self._endpoint, body, timeout_s)

        pages = pages_of(response, parser_id=self.id, blob_id=source.blob_id)
        if len(pages) != expected_pages:
            raise ProviderError(
                f"endpoint {self.id!r} returned {len(pages)} pages for {expected_pages} sent",
                reason="service",
                parser_id=self.id,
                blob_id=source.blob_id,
            )
        return pages

    def probe(self, timeout_s: float = 2.0) -> bool:
        """Whether the endpoint answers at all. Never raises: a health probe
        that could itself fail is not one a caller can rely on.
        """
        try:
            request = urllib.request.Request(
                f"{self._endpoint.url}/health", headers=dict(self._endpoint.headers)
            )
            with urllib.request.urlopen(request, timeout=timeout_s) as response:
                body = json.loads(response.read())
        except Exception:
            return False
        return bool(body.get("errorCode") == 0)
