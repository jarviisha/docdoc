"""``extract()`` -- the entry point that composes the layer.

Named ``extract`` rather than ``pipeline`` for the same reason ingest's entry
point is ``parse``: the Pipeline layer arrives at Milestone 7 and the name should
still be free.

The order is: resolve the schema, guard the budget, build the request, call the
adapter, check conformance, assemble the result. Every step before the adapter
call is local, which is what makes FR-041 true -- a request that was always going
to fail transmits nothing.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from docdoc.extraction.adapter import ExtractionOptions, ModelAdapter, ModelUsage
from docdoc.extraction.budget import guard_input_budget
from docdoc.extraction.conform import conform
from docdoc.extraction.errors import ExtractionError, ModelProviderError
from docdoc.extraction.identity import (
    EXTRACTOR_ID,
    extraction_artifact_id_for,
    options_hash_for_extraction,
)
from docdoc.extraction.observe import emit_extraction_event
from docdoc.extraction.prompt import build_request
from docdoc.extraction.shape import PROJECTION_ID, response_shape_for
from docdoc.extraction.value import ExtractedValue, ValueTree

if TYPE_CHECKING:
    from docdoc.extraction.registry import SchemaRegistry
    from docdoc.kernel import Document

__all__ = [
    "EXTRACTOR_VERSION",
    "ExtractionProvenance",
    "ExtractionResult",
    "extract",
    "extractor_version_for",
]

#: Bumped whenever this layer's own output changes for unchanged inputs (FR-036).
EXTRACTOR_VERSION = "1.0.0"


def extractor_version_for(adapter: ModelAdapter) -> str:
    """The processor version ADR-0003 folds into the artifact id.

    It **embeds the adapter's identity and version**, the way ingest's
    ``parser_version`` embeds the underlying library version (``1.0.0+pymupdf-…``)
    and for the same reason: the adapter builds the request and maps the response,
    so an adapter fix changes results, and a result-affecting input that does not
    reach the artifact id is the stale-cache bug ADR-0003 exists to prevent.

    Recording the adapter separately in provenance -- which this layer also does --
    makes the change *visible*. It does not make it *invalidating*. Only this does.
    """
    return f"{EXTRACTOR_VERSION}+{adapter.id}-{adapter.version}"


class ExtractionProvenance(BaseModel):
    """Everything needed to explain one result after the system has moved on.

    Recorded per result rather than kept beside it, for the reason Principle I
    gives about documents: a side-car record keyed by an id is separable from the
    thing it explains, which is precisely how provenance gets lost.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    document_id: str
    schema_identity: str
    schema_hash: str
    prompt_hash: str
    projection_id: str
    adapter_id: str
    adapter_version: str
    model_id: str
    model_version: str
    decoding: ExtractionOptions
    extractor_version: str
    usage: ModelUsage


class ExtractionResult(BaseModel):
    """One entry per declared field, plus provenance and artifact identity."""

    model_config = ConfigDict(frozen=True, extra="forbid", arbitrary_types_allowed=True)

    values: dict[str, Any]
    provenance: ExtractionProvenance
    artifact_id: str

    #: Undeclared fields the model returned, recorded rather than silently
    #: dropped (FR-008). Paths only -- never the values themselves.
    discarded: tuple[str, ...] = ()

    def value_at(self, path: str) -> ExtractedValue:
        """Fetch a scalar by dotted path, for callers that prefer it to indexing."""
        node: Any = self.values
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise KeyError(f"no field at {path!r}")
            node = node[part]
        if not isinstance(node, ExtractedValue):
            raise KeyError(f"{path!r} is a group, not a value")
        return node


def _count(tree: ValueTree) -> tuple[int, int]:
    """(present, absent) across the whole tree -- counts only, never content."""
    present = absent = 0
    for node in tree.values():
        if isinstance(node, ExtractedValue):
            if node.present:
                present += 1
            else:
                absent += 1
        elif isinstance(node, tuple):
            for entry in node:
                sub_present, sub_absent = _count(entry)
                present += sub_present
                absent += sub_absent
        else:
            sub_present, sub_absent = _count(node)
            present += sub_present
            absent += sub_absent
    return present, absent


def extract(
    document: Document,
    *,
    schema: str,
    registry: SchemaRegistry,
    adapter: ModelAdapter,
    options: ExtractionOptions | None = None,
) -> ExtractionResult:
    """Turn a document plus a schema identity into structured values.

    Exactly one result or an explicit error; never a partial result (FR-001).

    The document is read and never modified -- not its canonical text, not its
    provenance -- on the success path and on every failure path alike. That
    second half is Principle XII's "provider failure never corrupts the canonical
    document", and it holds here for a structural reason rather than a careful
    one: nothing in this function writes to ``document`` at all.
    """
    opts = options or ExtractionOptions()
    started = time.monotonic()
    # The kernel calls it `id`; everything downstream of ADR-0002 calls it a
    # document id, and the provenance field keeps that name.
    document_id = document.id

    entry = registry.resolve(schema)
    extractor_version = extractor_version_for(adapter)

    availability = adapter.available()
    if not availability.usable:
        raise ModelProviderError(
            f"adapter {adapter.id!r} is unavailable: {availability.reason}",
            reason="unavailable",
            adapter_id=adapter.id,
            document_id=document_id,
            schema_identity=entry.identity,
        )

    shape = response_shape_for(entry.schema)
    request = build_request(entry, document.text, response_shape=shape)

    estimate = guard_input_budget(
        request.rendered(),
        budget_tokens=opts.input_budget_tokens,
        document_id=document_id,
        schema_identity=entry.identity,
    )

    options_hash = options_hash_for_extraction(
        schema_identity=entry.identity,
        schema_hash=entry.schema_hash,
        prompt_hash=entry.prompt_hash,
        projection_id=PROJECTION_ID,
        model_id=getattr(adapter, "model_id", adapter.id),
        model_version=getattr(adapter, "model_version", adapter.version),
        max_tokens=opts.max_tokens,
        effort=str(opts.effort),
        thinking=str(opts.thinking),
        input_budget_tokens=opts.input_budget_tokens,
    )

    def _fail(reason: str) -> None:
        emit_extraction_event(
            document_id=document_id,
            schema_identity=entry.identity,
            schema_hash=entry.schema_hash,
            adapter_id=adapter.id,
            adapter_version=adapter.version,
            extractor_version=extractor_version,
            effort=str(opts.effort),
            estimated_input_tokens=estimate,
            duration_ms=round((time.monotonic() - started) * 1000, 3),
            outcome="failure",
            reason=reason,
        )

    try:
        response = adapter.complete(request, opts)
    except ModelProviderError as exc:
        _fail(exc.reason)
        raise
    except ExtractionError as exc:
        _fail(exc.reason)
        raise

    try:
        report = conform(
            response.payload,
            entry.schema,
            document_id=document_id,
            adapter_id=adapter.id,
        )
    except ExtractionError as exc:
        _fail(exc.reason)
        raise

    provenance = ExtractionProvenance(
        document_id=document_id,
        schema_identity=entry.identity,
        schema_hash=entry.schema_hash,
        prompt_hash=entry.prompt_hash,
        projection_id=PROJECTION_ID,
        adapter_id=adapter.id,
        adapter_version=adapter.version,
        model_id=response.model_id,
        model_version=response.model_version,
        decoding=opts,
        extractor_version=extractor_version,
        usage=response.usage,
    )
    artifact_id = extraction_artifact_id_for(
        document_id=document_id,
        extractor_id=EXTRACTOR_ID,
        extractor_version=extractor_version,
        options_hash=options_hash,
    )

    present, absent = _count(report.values)
    emit_extraction_event(
        document_id=document_id,
        schema_identity=entry.identity,
        schema_hash=entry.schema_hash,
        prompt_hash=entry.prompt_hash,
        artifact_id=artifact_id,
        adapter_id=adapter.id,
        adapter_version=adapter.version,
        model_id=response.model_id,
        model_version=response.model_version,
        extractor_version=extractor_version,
        effort=str(opts.effort),
        estimated_input_tokens=estimate,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cache_read_input_tokens=response.usage.cache_read_input_tokens,
        values_present=present,
        values_absent=absent,
        duration_ms=round((time.monotonic() - started) * 1000, 3),
        outcome="success",
    )

    return ExtractionResult(
        values=report.values,
        provenance=provenance,
        artifact_id=artifact_id,
        discarded=report.discarded,
    )
