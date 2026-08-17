"""Parser adapters — the only place in docdoc a provider SDK may be imported.

Each module here wraps one source of documents behind the
:class:`docdoc.ingest.parser.Parser` protocol. Nothing outside this package may
import ``pymupdf``, ``fitz``, or an ``azure`` SDK; ``import-linter`` fails the
build if it does (Constitution Principle IV).

Adapters are imported lazily by :func:`docdoc.ingest.registry.default_registry`,
so an uninstalled extra is a recorded unavailability rather than an ImportError.
"""

from __future__ import annotations

__all__: list[str] = []
