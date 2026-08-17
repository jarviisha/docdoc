"""Schema-driven extraction: a `Document` plus a versioned schema, in; values, out.

The layer owns the request, the conformance check, identity, and error
translation. It owns no judgment about whether an extracted value is *good*.

Two things it deliberately does not do, both for structural reasons rather than
for want of effort:

- **It resolves no grounding.** Every value carries the ADR-0004 grounding fields
  and every one of them is left unresolved -- not even the exact tier the
  kernel's existing search could satisfy cheaply. ADR-0003 makes grounding its
  own stage with its own artifact, so resolving it here would collapse two stages
  and fold a grounding input into this stage's identity. The consequence to
  accept is that when this ships, every extracted value is ungrounded.
- **It enforces no constraint.** A schema's numeric bounds and length limits are
  stored, hashed into its identity, and handed to Milestone 5. Extraction checks
  shape and type parseability only (Principle VII) -- and the provider could not
  enforce those constraints if asked, so no other split was available.

Nothing here names a provider. Which model answers is configuration, and the only
observable difference between two of them is in provenance.
"""

from __future__ import annotations

from docdoc.extraction.adapter import (
    Availability,
    ExtractionOptions,
    ModelAdapter,
    ModelResponse,
    ModelUsage,
)
from docdoc.extraction.errors import (
    ExtractionError,
    ModelProviderError,
    ProviderError,
    SchemaError,
)
from docdoc.extraction.extract import (
    ExtractionProvenance,
    ExtractionResult,
    extract,
)
from docdoc.extraction.identity import (
    prompt_hash_for,
    schema_hash_for,
)
from docdoc.extraction.loader import PromptTemplate, load_prompt, load_schema, prompt_path_for
from docdoc.extraction.registry import (
    RegisteredSchema,
    SchemaDescription,
    SchemaRegistry,
    default_registry,
)
from docdoc.extraction.schema import Cardinality, FieldSpec, FieldType, Schema
from docdoc.extraction.shape import PROJECTION_ID, response_shape_for
from docdoc.extraction.value import ExtractedValue

__all__ = [
    "PROJECTION_ID",
    "Availability",
    "Cardinality",
    "ExtractedValue",
    "ExtractionError",
    "ExtractionOptions",
    "ExtractionProvenance",
    "ExtractionResult",
    "FieldSpec",
    "FieldType",
    "ModelAdapter",
    "ModelProviderError",
    "ModelResponse",
    "ModelUsage",
    "PromptTemplate",
    "ProviderError",
    "RegisteredSchema",
    "Schema",
    "SchemaDescription",
    "SchemaError",
    "SchemaRegistry",
    "default_registry",
    "extract",
    "load_prompt",
    "load_schema",
    "prompt_hash_for",
    "prompt_path_for",
    "response_shape_for",
    "schema_hash_for",
]
