"""The built browser client, as an installable package.

This module holds no logic and is not imported by ``docdoc`` for its behaviour.
It exists so that ``docdoc.api.ui`` can find the assets the way Python finds
anything — by importing the package and reading its ``__file__`` — rather than by
guessing at a filesystem layout.

``assets/`` is populated by ``build.sh`` from ``ui/dist`` immediately before the
wheel is built. It is absent from a source checkout on purpose: FR-038 forbids
committing build output, so an installed copy of this package is the only place
the assets ever exist at rest.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["ASSETS", "__version__"]

__version__ = "0.1.0"

#: Where the built interface lives inside an installed copy of this package.
ASSETS = Path(__file__).parent / "assets"
