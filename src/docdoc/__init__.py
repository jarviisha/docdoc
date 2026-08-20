"""docdoc — a provider-agnostic Intelligent Document Processing engine.

Turns unstructured documents into structured, validated data while preserving
source-level provenance through the whole pipeline. There is no single entry
point: each layer is imported directly, and they compose downward.

:mod:`docdoc.kernel`
    The canonical Document IR and its deterministic operations --
    ``locate``, ``find``, ``slice``, ``merge`` -- plus the two-level identity
    model of ADR-0002. Depends on ``pydantic`` alone.
:mod:`docdoc.ingest`
    Bytes to a ``Document``, by declared capability rather than by provider name.
:mod:`docdoc.extraction`
    A schema and a model adapter to typed values. What the model answered.
:mod:`docdoc.grounding`
    Those values resolved to spans, pages, and boxes. Where each one is.
:mod:`docdoc.validation`
    The verdict on whether the result is acceptable. Whether to trust it.

The base install carries ``pydantic`` and ``rapidfuzz`` and no provider SDK;
providers are optional extras. See the roadmap in ``README.md`` for what each
milestone added and what is still ahead.
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
