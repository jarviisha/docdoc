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

from pydantic import BaseModel, ConfigDict, model_validator

from docdoc.extraction.adapter import ExtractionOptions, ModelAdapter, ModelUsage
from docdoc.extraction.budget import guard_input_budget
from docdoc.extraction.conform import conform
from docdoc.extraction.errors import ExtractionError, ModelProviderError, SchemaError
from docdoc.extraction.identity import (
    EXTRACTOR_ID,
    extraction_artifact_id_for,
    options_hash_for_extraction,
)
from docdoc.extraction.observe import emit_extraction_event
from docdoc.extraction.prompt import build_request
from docdoc.extraction.retry import call_with_retries
from docdoc.extraction.shape import PROJECTION_ID, response_shape_for
from docdoc.extraction.value import ExtractedValue, ValueTree

# From the submodule, never the package facade: `docdoc.ingest` reaches
# parse -> registry -> parsers -> pymupdf and azure, which would put two provider
# SDKs in this layer's transitive graph. This is the second time that mistake was
# made here; `test_extraction_boundaries.py` now asserts the submodule form so
# there is no third.
from docdoc.ingest.options import TransportSettings

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

    @model_validator(mode="before")
    @classmethod
    def _rebuild_values(cls, data: Any) -> Any:
        """Turn a deserialised value tree back into ``ExtractedValue`` nodes.

        ``values`` is ``dict[str, Any]`` because ``ValueTree`` is recursive by
        declaration, and ``Any`` means pydantic hands back the plain dictionaries
        it read rather than the models they describe. Every consumer then sees a
        mapping where it expected a value: ``value_at`` raises, ``grounded``
        does not exist, and grounding reads a claim off a ``dict`` that has no
        ``claimed_text`` attribute.

        Milestone 6 met this first and solved it inside
        ``evaluation/predictions.py``, where a result read from disk scored as
        entirely missing. Milestone 7 meets it again, because a reused stage is
        also a result read from disk — and FR-012 requires a reused result to be
        indistinguishable from the executed one, which "equal once serialised"
        is not. Fixing it on the model rather than in a third caller is what
        stops it being met a fourth time.

        Deliberately no type coercion: JSON cannot tell an int from a Decimal,
        and deciding which one a field wanted needs the schema. That stays in
        ``evaluation``, which has the declared types to do it with.
        """
        if isinstance(data, dict) and isinstance(data.get("values"), dict):
            data = {**data, "values": _rebuild_tree(data["values"])}
        return data

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


def _rebuild_tree(node: Any) -> Any:
    """Walk a value tree, rebuilding every leaf that is a serialised value.

    A leaf is recognised by carrying ``field_path`` and ``present`` together —
    the two fields every ``ExtractedValue`` has and no group node does. Matching
    on both rather than on ``field_path`` alone is the cheap guard against a
    schema that happens to declare a field of that name.
    """
    if isinstance(node, ExtractedValue):
        return node
    if isinstance(node, dict):
        if "field_path" in node and "present" in node:
            return ExtractedValue.model_validate(node)
        return {name: _rebuild_tree(child) for name, child in node.items()}
    if isinstance(node, (list, tuple)):
        # A repeating group. Restored as a tuple, because that is what the
        # extraction layer builds and what `ValueTree` declares; a list here
        # would compare unequal to the executed result for no reason.
        return tuple(_rebuild_tree(child) for child in node)
    return node


def _requested_model(adapter: ModelAdapter) -> tuple[str, str]:
    """The model an adapter says it *will* reach, before any response exists.

    Used only for the failure event. A failed call has no response to read the
    reached model from, and "which model were we even trying?" is still the
    question a reader of the log has -- particularly for the failures that happen
    before the call, where the answer is the whole diagnosis.

    The success path deliberately does **not** use this: it reads the model the
    provider reports, because the two differ whenever a request names an alias
    and only one of them is the model that actually answered.

    ``ModelAdapter`` does not require either property. An adapter may have no
    notion of a model separate from itself, so its own identity is the fallback.
    """
    return (
        getattr(adapter, "model_id", adapter.id),
        getattr(adapter, "model_version", adapter.version),
    )


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
    transport: TransportSettings | None = None,
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
    settings = transport or TransportSettings()
    started = time.monotonic()
    # The kernel calls it `id`; everything downstream of ADR-0002 calls it a
    # document id, and the provenance field keeps that name.
    document_id = document.id

    extractor_version = extractor_version_for(adapter)
    requested_model_id, requested_model_version = _requested_model(adapter)

    # What the event can say so far. These fill in as the extraction gets further,
    # and `_fail` reads them at call time, so a failure before the schema resolves
    # emits an event that simply omits what was not known yet rather than no event
    # at all. `emit_extraction_event` drops `None`, so absence stays absence.
    schema_identity: str | None = None
    schema_hash: str | None = None
    estimate: int | None = None

    def _fail(reason: str, *, attempts: int) -> None:
        # `attempts` has no default on purpose. It counts calls the *model*
        # received, so every failure path has to state its own: the ones that fail
        # before transmission made zero, and a default of one would report an
        # attempt against the very requests SC-016 guarantees sent nothing. Making
        # it required turns that into a decision at each site rather than a
        # number inherited by whoever adds the next early return (FR-040).
        emit_extraction_event(
            attempts=attempts,
            document_id=document_id,
            schema_identity=schema_identity,
            schema_hash=schema_hash,
            adapter_id=adapter.id,
            adapter_version=adapter.version,
            # The model *requested*. A failed call reached no model, so there is
            # no reported one to record, and "which model was this aimed at?" is
            # what a reader of a failure needs (FR-040).
            model_id=requested_model_id,
            model_version=requested_model_version,
            extractor_version=extractor_version,
            temperature=opts.temperature,
            seed=opts.seed,
            estimated_input_tokens=estimate,
            duration_ms=round((time.monotonic() - started) * 1000, 3),
            outcome="failure",
            reason=reason,
        )

    # Everything here is local, which is what makes FR-041 true -- a request that
    # was always going to fail transmits nothing. It is wrapped as one block
    # because FR-040 says *every* extraction emits an event, and these three
    # failures -- an unregistered schema, a missing credential, an over-budget
    # document -- are exactly the ones caused by the caller's own configuration
    # and therefore the ones most worth seeing in aggregate.
    try:
        entry = registry.resolve(schema)
        schema_identity = entry.identity
        schema_hash = entry.schema_hash

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
        request = build_request(entry, document.text, response_shape=shape, document_id=document_id)

        estimate = guard_input_budget(
            request.rendered(),
            budget_tokens=opts.input_budget_tokens,
            document_id=document_id,
            schema_identity=entry.identity,
        )
    except SchemaError:
        # `SchemaError` carries no `reason`; resolution is the only way to reach
        # it from here, and naming it as such beats inventing a taxonomy.
        _fail("schema", attempts=0)
        raise
    except (ExtractionError, ModelProviderError) as exc:
        # Zero: nothing here has spoken to the model. That is the whole point of
        # doing this work first (FR-041), and the event should say so.
        _fail(exc.reason, attempts=0)
        raise

    attempts = 0
    try:
        response, attempts = call_with_retries(
            lambda: adapter.complete(request, opts),
            transport=settings,
            adapter_id=adapter.id,
            document_id=document_id,
            schema_identity=entry.identity,
        )
    except ModelProviderError as exc:
        _fail(exc.reason, attempts=exc.attempts)
        raise
    except ExtractionError as exc:
        # Not a provider failure, so the retry loop never counted it: an adapter
        # raised this while mapping a response it had already received. Exactly
        # one call reached the model.
        _fail(exc.reason, attempts=1)
        raise

    try:
        report = conform(
            response.payload,
            entry.schema,
            document_id=document_id,
            adapter_id=adapter.id,
        )
    except ExtractionError as exc:
        # The call succeeded and the answer did not conform. `attempts` is the
        # real count from the retry loop, which may be more than one if the
        # model was reached only after transient failures.
        _fail(exc.reason, attempts=attempts)
        raise

    # Folded from the model the provider *reported*, not the one requested -- the
    # same values provenance records below, and deliberately so. The two differ
    # whenever a request names an alias or a provider rolls a point release under
    # an unchanged name, and folding the requested one would let two genuinely
    # different computations share a content address (FR-034, FR-035, SC-009).
    #
    # Reading it after the call is free: the artifact id is derived here, not used
    # to look anything up beforehand. This feature stores and caches nothing (the
    # spec's "the artifact identity is computed, not stored"), so there is no
    # pre-call lookup that would need an identity before there is a response.
    options_hash = options_hash_for_extraction(
        schema_identity=entry.identity,
        schema_hash=entry.schema_hash,
        prompt_hash=entry.prompt_hash,
        projection_id=PROJECTION_ID,
        model_id=response.model_id,
        model_version=response.model_version,
        max_output_tokens=opts.max_output_tokens,
        temperature=opts.temperature,
        top_p=opts.top_p,
        top_k=opts.top_k,
        seed=opts.seed,
        thinking_budget=opts.thinking_budget,
        input_budget_tokens=opts.input_budget_tokens,
    )

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
        temperature=opts.temperature,
        seed=opts.seed,
        estimated_input_tokens=estimate,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        cache_read_input_tokens=response.usage.cache_read_input_tokens,
        values_present=present,
        values_absent=absent,
        attempts=attempts,
        reasoning_tokens=response.usage.reasoning_tokens,
        duration_ms=round((time.monotonic() - started) * 1000, 3),
        outcome="success",
    )

    return ExtractionResult(
        values=report.values,
        provenance=provenance,
        artifact_id=artifact_id,
        discarded=report.discarded,
    )
