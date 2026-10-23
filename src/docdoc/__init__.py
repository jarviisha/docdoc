"""docdoc — a provider-agnostic Intelligent Document Processing engine.

A self-hosted service (HTTP API, operations console, Docker image) that turns
unstructured documents into structured, validated data while preserving
source-level provenance. These packages are its implementation, not a public
library; they compose downward:

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

Provider SDKs live only inside adapter directories (import-linter enforces it).
"""

__version__ = "0.1.0"

__all__ = ["__version__"]
