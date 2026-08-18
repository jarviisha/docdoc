"""Model adapters -- the only place a provider SDK may be imported.

This file was missing until the second analysis pass caught it. Python treated the
directory as a namespace package, so ``adapters.echo`` kept importing and nothing
broke loudly; what *did* break was ``from docdoc.extraction.adapters import
EchoAdapter``, which three documents taught and which failed with "unknown
location". A package that works by accident is a package whose behaviour nobody
chose.

``EchoAdapter`` is exported because it is public surface: it is what makes the
whole extraction path runnable with no credentials, and the documented example
depends on it.

``GeminiAdapter`` is deliberately **not** exported. Which provider answers is
configuration, and a name in this file would read as the default. It is imported
by its own path, which also keeps the SDK out of this module's import graph.
"""

from __future__ import annotations

from docdoc.extraction.adapters.echo import EchoAdapter

__all__ = ["EchoAdapter"]
