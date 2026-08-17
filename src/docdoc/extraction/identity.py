"""Schema, prompt, and extraction-artifact identity.

Two identities answer two different questions, and neither substitutes for the
other (ADR-0008):

``schema_version``
    The major integer in ``name@version``. Author-assigned. Answers *did the
    consumer contract change?*
``schema_hash``
    Derived over the schema's canonical form. Answers *did anything
    result-affecting change?*

Both are folded into the extract stage's ``options_hash``, which is what makes a
reworded field description invalidate the extraction artifact while reusing the
parse -- the outcome keying on the integer alone cannot express.

The canonical form is the one ADR-0002 already defines and the kernel already
implements. Reusing ``canonical_json`` rather than inventing a second convention
is FR-013, and it means key ordering and float formatting are already settled.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from docdoc.kernel import canonical_json, options_hash_for

if TYPE_CHECKING:
    from docdoc.extraction.schema import FieldSpec, Schema

__all__ = [
    "EXTRACTOR_ID",
    "extraction_artifact_id_for",
    "options_hash_for_extraction",
    "prompt_hash_for",
    "schema_hash_for",
]

#: The processor id of this stage in the ADR-0003 chain. Stable; the *version*
#: is what moves when output changes for fixed inputs.
EXTRACTOR_ID = "schema-extractor"


def _field_payload(field: FieldSpec) -> dict[str, Any]:
    """The hashable form of one field.

    Everything that can change a result is here, including ``description`` (it
    steers the model) and ``constraints`` (they change what a result *means* to
    the validation stage). Nothing that cannot is.
    """
    return {
        "name": field.name,
        "type": None if field.type is None else str(field.type),
        "cardinality": str(field.cardinality),
        "required": field.required,
        "description": field.description,
        "constraints": dict(field.constraints),
        "fields": _sorted_payloads(field.fields),
    }


def _sorted_payloads(fields: tuple[FieldSpec, ...]) -> list[dict[str, Any]]:
    """Field payloads ordered by name, at every depth.

    Declaration order is *presentation*, not meaning: a schema declares a set of
    fields, and moving one up the file changes nothing about what a result means.
    SC-005 requires two schemas differing only in field order to hash
    identically, and canonical JSON sorts object keys but preserves list order --
    so the sort has to happen here rather than being inherited from ADR-0002.
    """
    return sorted((_field_payload(child) for child in fields), key=lambda item: str(item["name"]))


def schema_hash_for(schema: Schema) -> str:
    """``sha256`` over the whole schema's canonical form (EXT-6).

    Deliberately excludes ``name`` and ``version``: the hash answers "did
    anything result-affecting change?", and a pure ``@1`` -> ``@2`` bump with no
    other edit did not change the result (EXT-9). The identity is folded
    separately, so the two artifacts still differ.

    It covers *more* than the wire projection does. A numeric bound is hashed
    here and dropped from the request the provider sees, so editing one
    invalidates the extraction cache while changing nothing the model reads.
    That is correct under ADR-0008 and it looks like a spurious cache miss
    (research.md R3).
    """
    return options_hash_for({"fields": _sorted_payloads(schema.fields)})


def prompt_hash_for(text: str) -> str:
    """``sha256`` over a prompt's canonical form."""
    return options_hash_for({"prompt": text})


def options_hash_for_extraction(
    *,
    schema_identity: str,
    schema_hash: str,
    prompt_hash: str,
    projection_id: str,
    model_id: str,
    model_version: str,
    max_output_tokens: int,
    temperature: float,
    top_p: float | None,
    top_k: int | None,
    seed: int | None,
    thinking_budget: int | None,
    input_budget_tokens: int,
) -> str:
    """Every input that can change a successful extraction, and nothing else.

    ADR-0003's ``Extract`` row names "temperature, top_p, seed, max_tokens", and on
    the chosen provider every one of them exists -- so the row is followed rather
    than refined (research.md R4). ``top_k`` and ``thinking_budget`` are folded too:
    the ADR's list is a minimum, and both change the answer.

    A ``seed`` is folded even though the provider treats it as best-effort. Two runs
    under one seed may still differ; two runs under *different* seeds are a different
    request, and an input that can change the result must reach the identity whether
    or not it fully determines it.

    Retry, timeout, and deadline are absent by construction: they live in
    ``TransportSettings``, a separate type, so FR-027 holds without discipline.
    """
    return options_hash_for(
        {
            "schema_identity": schema_identity,
            "schema_hash": schema_hash,
            "prompt_hash": prompt_hash,
            "projection_id": projection_id,
            "model_id": model_id,
            "model_version": model_version,
            "max_output_tokens": max_output_tokens,
            "temperature": temperature,
            "top_p": top_p,
            "top_k": top_k,
            "seed": seed,
            "thinking_budget": thinking_budget,
            "input_budget_tokens": input_budget_tokens,
        }
    )


def extraction_artifact_id_for(
    *,
    document_id: str,
    extractor_id: str,
    extractor_version: str,
    options_hash: str,
) -> str:
    """The extract stage's link in the ADR-0003 chain.

    Derived from the *document's* artifact id, so the chain composes: changing
    only the schema reuses the parse and never re-runs an ingest provider
    (EXT-23).
    """
    return options_hash_for(
        {
            "input_artifact_id": document_id,
            "processor_id": extractor_id,
            "processor_version": extractor_version,
            "options_hash": options_hash,
        }
    )


def canonical_bytes(value: Any) -> bytes:
    """Re-exported so callers need not reach into the kernel for one function."""
    return canonical_json(value)
