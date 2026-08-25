"""The ingest entry point: bytes in, Document out.

Named ``parse`` rather than ``pipeline`` so it does not collide with the Pipeline
layer that arrives at Milestone 7 -- these are different things at different
altitudes, and one name for both would confuse every later discussion of the
dependency order.

This module composes; it decides very little. Type detection lives in ``source``,
the routing verdict in ``assess``, parser choice in ``registry``, coordinate and
text handling in ``normalize``, and output checking in ``validate``. What lives
here is the order those happen in, and the guarantee that every exit -- success or
failure -- is typed and logged exactly once.
"""

from __future__ import annotations

import time

# Runtime imports, not TYPE_CHECKING ones: `ParsePlan` is a NamedTuple, and
# typing resolves a NamedTuple's field annotations when the class body executes.
# Under TYPE_CHECKING these names would not exist at that moment.
from collections.abc import Mapping
from typing import Any, Literal, NamedTuple

from docdoc.ingest.assess import TextLayerRule, assess_text_layer
from docdoc.ingest.capabilities import CapabilityRequest
from docdoc.ingest.errors import ParserCapabilityError, UnsupportedDocumentError
from docdoc.ingest.observe import log_parse
from docdoc.ingest.options import TransportSettings, options_fingerprint
from docdoc.ingest.parser import Parser
from docdoc.ingest.registry import ParserRegistry, default_registry
from docdoc.ingest.source import Limits, SourceFile
from docdoc.ingest.validate import validate_output
from docdoc.kernel import Document, TextLayerRecord

__all__ = ["ParsePlan", "execute_plan", "parse", "plan_parse"]

Path = Literal["native", "recognition"]


class ParsePlan(NamedTuple):
    """Everything decided about a parse before the parser runs.

    The seam the artifact chain needed. ``document_id`` is
    ``f(blob_id, parser_id, parser_version, options_hash)``, and ``parser_id``
    comes from routing — which reads the file. So the identity of a parse is not
    knowable until routing and selection have happened, and *is* knowable before
    the parser executes. That gap is exactly one function call wide, and this is
    it (research R2, FR-061).

    What that buys: a cached parse skips the parser, including a billable
    service-backed one, while the text-layer verdict is still computed and
    recorded on every run. Principle V's routing decision stays inspectable on a
    cache hit, which it would not be if the whole call were skipped.
    """

    file: SourceFile
    parser: Parser
    verdict: TextLayerRecord
    #: The canonical options mapping, as it will be stored in provenance.
    options: Mapping[str, Any]
    #: Its hash, which is the fourth input to ``document_id_for``.
    options_hash: str
    transport: TransportSettings
    limits: Limits

    @property
    def document_id(self) -> str:
        """The identity this parse will produce, known before it produces it."""
        from docdoc.kernel.identity import document_id_for

        return document_id_for(
            blob_id=self.file.blob_id,
            parser_id=self.parser.id,
            parser_version=self.parser.version,
            options_hash=self.options_hash,
        )


def parse(
    source: SourceFile | bytes,
    *,
    require: CapabilityRequest | None = None,
    options: Mapping[str, Any] | None = None,
    transport: TransportSettings | None = None,
    registry: ParserRegistry | None = None,
    force: Path | None = None,
    limits: Limits | None = None,
    rule: TextLayerRule | None = None,
) -> Document:
    """Turn a source file into a canonical Document.

    Args:
        source: The bytes, or a ``SourceFile`` if the caller already built one.
        require: The capabilities needed. Defaults to text plus geometry, which
            is what makes a result groundable.
        options: Knobs that can change what the parse produces. Part of the
            document's identity.
        transport: How a service-backed parse talks to its service. Never part
            of identity.
        registry: The parsers this call may choose from. Defaults to whichever
            adapters this installation can use.
        force: Take a specific path instead of the one the text-layer rule
            chooses. The rule still runs where it can, and its verdict is kept.
            Where the rule cannot run at all -- no native reader installed -- an
            explicit ``force`` is the supported way to parse a PDF anyway.
        limits: Size, page-count, and media-type limits. Enforced before any
            parse or transmission.
        rule: The text-layer rule and its thresholds. Both are configurable;
            changing a default requires a new rule id, so past verdicts stay
            interpretable.

    Returns:
        A Document satisfying every kernel invariant, with provenance recording
        which parser produced it and why that parser was chosen.

    Raises:
        UnsupportedDocumentError: the file cannot be accepted at all.
        ParserCapabilityError: no available parser satisfies the request.
        ParserError: a parser produced something invalid.
        ProviderError: a service-backed parse failed.
    """
    started = time.monotonic()
    plan = plan_parse(
        source,
        require=require,
        options=options,
        transport=transport,
        registry=registry,
        force=force,
        limits=limits,
        rule=rule,
        started=started,
    )
    return execute_plan(plan, started=started)


def plan_parse(
    source: SourceFile | bytes,
    *,
    require: CapabilityRequest | None = None,
    options: Mapping[str, Any] | None = None,
    transport: TransportSettings | None = None,
    registry: ParserRegistry | None = None,
    force: Path | None = None,
    limits: Limits | None = None,
    rule: TextLayerRule | None = None,
    started: float | None = None,
) -> ParsePlan:
    """Everything up to and not including running the parser.

    Reads the file, routes it, selects a parser, and canonicalizes the options —
    so ``plan.document_id`` is available without a parse having happened. That is
    what lets the pipeline ask the store "do I already have this?" before paying
    for the expensive half (FR-061).

    Cheap and local by construction: routing reads page text and the selection is
    a capability match. No credentials, no network, and no provider call happen
    here, which is what FR-059 requires of every identity computation.
    """
    limits = limits or Limits()
    transport = transport or TransportSettings()
    rule = rule or TextLayerRule()
    registry = registry if registry is not None else default_registry()
    started = time.monotonic() if started is None else started

    file = (
        source if isinstance(source, SourceFile) else SourceFile.from_bytes(source, limits=limits)
    )
    # Unconditional, and idempotent for the bytes path. A caller may hand in a
    # `SourceFile` built earlier under different limits, or none it remembers,
    # and `limits` is documented here as enforced. Re-checking costs a comparison
    # and removes the branch where it could be forgotten.
    file.check_limits(limits)
    verdict: TextLayerRecord | None = None

    try:
        verdict = _route(file, rule=rule, force=force)
        if verdict.pages:
            # The page count is known as soon as the rule has run, which is the
            # earliest point it can be checked (ING-2).
            file.check_page_count(len(verdict.pages), limits)
        parser = _select(registry, file, require=require, native=verdict.text_layer_usable)
        canonical, options_hash = options_fingerprint(options)
    except Exception as error:
        _log_failure(error, file=file, verdict=verdict, started=started)
        raise

    return ParsePlan(
        file=file,
        parser=parser,
        verdict=verdict,
        options=canonical,
        options_hash=options_hash,
        transport=transport,
        limits=limits,
    )


def execute_plan(plan: ParsePlan, *, started: float | None = None) -> Document:
    """Run the parser a plan selected, and check what it produced.

    Separated from :func:`plan_parse` so the pipeline can skip *this* half on a
    cache hit while still paying for the other. Everything expensive is here:
    the parse itself, and for a service-backed parser the network call and the
    bill that comes with it.
    """
    started = time.monotonic() if started is None else started
    file, parser, verdict = plan.file, plan.parser, plan.verdict

    try:
        document = parser.parse(file, dict(plan.options), plan.transport, verdict)
        if not verdict.pages:
            # A skipped rule left the page count unknown, so this is the first
            # moment it exists. Checking here still stops an over-limit document
            # from becoming a `Document`; it cannot undo a transmission a remote
            # parse has already made, which is why the size limit -- enforced
            # before anything leaves the process -- is the one that bounds cost.
            file.check_page_count(len(document.pages), plan.limits)
        validate_output(
            document,
            parser.capabilities,
            parser_id=parser.id,
            blob_id=file.blob_id,
            parser_version=parser.version,
            text_layer=verdict,
        )
    except Exception as error:
        _log_failure(error, file=file, verdict=verdict, started=started)
        raise

    log_parse(
        blob_id=file.blob_id,
        media_type=file.media_type,
        outcome="ok",
        duration_ms=_elapsed_ms(started),
        document_id=document.id,
        parser_id=parser.id,
        parser_version=parser.version,
        text_layer_usable=document.provenance.text_layer_used,
        text_layer_rule=verdict.rule_id,
        pages=len(document.pages),
    )
    return document


def _log_failure(
    error: BaseException,
    *,
    file: SourceFile,
    verdict: TextLayerRecord | None,
    started: float,
) -> None:
    """One event per failed parse, wherever in the two halves it failed.

    Deliberately broader than ``IngestError``. FR-040 asks for one event per
    parse, and the failures that leave the error model are exactly the ones worth
    a trace -- an unexpected exception with no record of the parse that caused it
    is the hardest kind to chase. Logged and re-raised unchanged by the caller:
    this is a witness, not a handler.

    Extracted when ``parse`` split in two (research R2). Both halves can fail and
    both owe the same event, and two copies of it would have drifted.
    """
    log_parse(
        blob_id=file.blob_id,
        media_type=file.media_type,
        outcome="error",
        duration_ms=_elapsed_ms(started),
        parser_id=getattr(error, "parser_id", None),
        text_layer_usable=verdict.text_layer_usable if verdict else None,
        text_layer_rule=verdict.rule_id if verdict else None,
        error_type=type(error).__name__,
        error_reason=getattr(error, "reason", None),
    )


def _route(file: SourceFile, *, rule: TextLayerRule, force: Path | None) -> TextLayerRecord:
    """Decide which path this document takes, and record why.

    The decision is made *before* a parser is chosen and is always recorded,
    including when a caller overrides it -- an override replaces the routing, not
    the evidence (FR-009, FR-012).
    """
    if force is None:
        return assess_text_layer(file, rule=rule)

    forced_native = force == "native"
    try:
        assessed = assess_text_layer(file, rule=rule)
    except ParserCapabilityError:
        # The rule could not run, but the caller said which path they want. That
        # is not a guess, so it is honoured -- and the emptiness is explained
        # rather than left to be inferred.
        return rule.skipped("reader_unavailable", overridden=True)

    return rule.as_override(assessed, forced_native=forced_native)


def _elapsed_ms(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _select(
    registry: ParserRegistry,
    file: SourceFile,
    *,
    require: CapabilityRequest | None,
    native: bool,
) -> Parser:
    """Choose a parser for the routed path, by capability.

    The routing verdict narrows the field: a document whose text layer is not
    usable needs a parser that does not depend on one, which is expressed as
    ``requires_network`` rather than by naming a provider. There is no fallback
    between the two paths -- a refusal here is explicit (FR-014, FR-017).
    """
    request = require or CapabilityRequest(media_type=file.media_type, geometry=True)
    if request.media_type != file.media_type:
        # This used to rewrite the request to match the bytes and carry on, so a
        # caller asking for a PNG parser on a PDF got a PDF parse and no word
        # about it. The bytes do decide what the file is (ING-1) -- but silently
        # answering a question nobody asked is the habit this project rejects
        # everywhere else, and the caller's own belief about the file is worth
        # correcting out loud.
        raise UnsupportedDocumentError(
            f"the request asks for {request.media_type} but the bytes are "
            f"{file.media_type}; docdoc decides the type from the bytes, so drop "
            "the media_type from your request or correct it",
            reason="mime_type",
            blob_id=file.blob_id,
            media_type=file.media_type,
        )

    parser = registry.select(request)
    if native or parser.capabilities.requires_network:
        return parser

    # The chosen parser reads text layers, and this document has none worth
    # reading. Say so rather than handing back a near-empty document that looks
    # like a successful parse.
    raise ParserCapabilityError(
        "this document has no usable text layer and needs a recognition-backed "
        f"parser; {parser.id} reads native text only",
        required=request.required_names(),
        media_type=file.media_type,
        candidates=tuple(
            (entry.id, entry.available, entry.unavailable_reason)
            for entry in registry.candidates_all()
        ),
        blob_id=file.blob_id,
    )
