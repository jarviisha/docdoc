"""Each stage's artifact id, computed *before* the stage runs.

This is the half of ADR-0003 that six milestones deferred. Every stage below
already derives its own artifact id at the end of its work, which is enough to
*record* an identity and useless for *reusing* one: by the time the id exists,
the cloud parser has been billed and the model has answered. FR-012 requires the
identity to come from inputs known beforehand, and this module is where those
inputs are gathered.

**It delegates every derivation.** The options-hash functions here are the same
ones each layer calls on its own way out — ``options_hash_for_extraction``,
``options_hash_for_grounding``, ``options_hash_for_validation`` — invoked with the
inputs read from the registry, the adapter, and the options rather than from a
result that does not exist yet. Reimplementing a fold here would put the folded
set in two places, and the moment they disagreed the store would answer with an
artifact for inputs that had changed (FR-058).

**Nothing here needs a credential, a network, or a provider** (FR-059). Resolving
a schema, reading an adapter's declared model, and canonicalizing options are all
local, which is what makes a fully reused run succeed with nothing configured.

**The extract stage is the awkward one, and deliberately so.** Its options hash
folds the model that *answered*, which Milestone 3 chose on purpose: a request
naming an alias and the model that served it are different computations, and
folding the requested name would let them share one content address. That was
sound while nothing cached, and it is what makes the id un-precomputable now. The
resolution is not to weaken the id but to guess it: :func:`extract_artifact_id`
returns the id the run *will* produce **if the provider answers with the model it
was asked for**, and the runner only stores a result whose real id matches that
guess. A right guess is a cache entry; a wrong one is a provider that substituted
a model, and a result that must not be filed under the identity of a different
one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

__all__ = [
    "extract_artifact_id",
    "ground_artifact_id",
    "validate_artifact_id",
]

if TYPE_CHECKING:
    from docdoc.extraction.adapter import ExtractionOptions
    from docdoc.grounding.options import GroundingOptions
    from docdoc.validation.options import ValidationOptions


def extract_artifact_id(
    document_id: str,
    *,
    schema: str,
    registry: Any,
    adapter: Any,
    options: ExtractionOptions | None = None,
) -> str | None:
    """The extract artifact id this run will produce if the model behaves.

    Returns ``None`` when the id cannot be computed at all — an unresolvable
    schema, an adapter that declares no model — in which case the runner simply
    does not consult the store and lets the stage raise its own typed error. A
    lookup is an optimisation and must never be the thing that reports a fault.
    """
    from docdoc.extraction.adapter import ExtractionOptions
    from docdoc.extraction.extract import extractor_version_for
    from docdoc.extraction.identity import (
        EXTRACTOR_ID,
        extraction_artifact_id_for,
        options_hash_for_extraction,
    )
    from docdoc.extraction.shape import PROJECTION_ID

    opts = options or ExtractionOptions()

    try:
        entry = registry.resolve(schema)
    except Exception:
        # Including SchemaError. The stage will raise it again in a moment, from
        # the layer that owns it, with the message that layer wrote.
        return None

    # The model *requested*. `extract()` folds the model reported by the
    # response, and the runner compares the two before storing anything.
    model_id = getattr(adapter, "model_id", adapter.id)
    model_version = getattr(adapter, "model_version", adapter.version)

    options_hash = options_hash_for_extraction(
        schema_identity=entry.identity,
        schema_hash=entry.schema_hash,
        prompt_hash=entry.prompt_hash,
        projection_id=PROJECTION_ID,
        model_id=model_id,
        model_version=model_version,
        max_output_tokens=opts.max_output_tokens,
        temperature=opts.temperature,
        top_p=opts.top_p,
        top_k=opts.top_k,
        seed=opts.seed,
        thinking_budget=opts.thinking_budget,
        input_budget_tokens=opts.input_budget_tokens,
    )
    return extraction_artifact_id_for(
        document_id=document_id,
        extractor_id=EXTRACTOR_ID,
        extractor_version=extractor_version_for(adapter),
        options_hash=options_hash,
    )


def ground_artifact_id(
    extraction_artifact_id: str, *, options: GroundingOptions | None = None
) -> str:
    """The grounding artifact id, which is knowable outright.

    Grounding is deterministic and takes no provider, so its id folds only its
    input artifact and its own options. Nothing here can be wrong later.
    """
    from docdoc.grounding.identity import grounding_artifact_id_for
    from docdoc.grounding.options import GroundingOptions

    return grounding_artifact_id_for(
        extraction_artifact_id=extraction_artifact_id,
        options=options or GroundingOptions(),
    )


def validate_artifact_id(
    grounding_artifact_id: str,
    *,
    schema: str,
    registry: Any,
    options: ValidationOptions | None = None,
) -> str | None:
    """The validation artifact id, folding the rules this run will evaluate.

    ``enabled_rules`` is resolved from the schema rather than echoed from the
    options, exactly as the validation layer resolves it — so "every rule" and
    "these three, which happen to be all of them" produce one identity rather
    than two (FR-048). It is read from the registry here because the schema is
    the thing that declares them, and the schema is available before the stage.
    """
    from docdoc.validation import resolve_enabled_rules
    from docdoc.validation.identity import (
        options_hash_for_validation,
        validation_artifact_id_for,
    )
    from docdoc.validation.options import ValidationOptions

    opts = options or ValidationOptions()

    try:
        resolved = resolve_enabled_rules(registry.resolve(schema).schema, opts)
    except Exception:
        return None

    return validation_artifact_id_for(
        grounding_artifact_id=grounding_artifact_id,
        options_hash=options_hash_for_validation(opts, enabled_rules=resolved),
    )
