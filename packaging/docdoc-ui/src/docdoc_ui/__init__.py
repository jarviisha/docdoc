"""The built browser client, as an installable package.

This module holds no logic and is not imported by ``docdoc`` for its behaviour.
It exists so that ``docdoc.api.ui`` can find the assets the way Python finds
anything — by importing the package and reading its ``__file__`` — rather than by
guessing at a filesystem layout.

``assets/`` is populated by ``build.sh`` from ``ui/dist`` immediately before the
wheel is built. It is absent from a source checkout on purpose: FR-038 forbids
committing build output, so an installed copy of this package is the only place
the assets ever exist at rest.

``console/`` is the same thing for Milestone 11's operations console, built from
``ui/dist-console``. Two trees rather than one because Vite's ``base`` is
per-build and the console is served from a different path for a reason that is
not cosmetic — see ``specs/011-operations-console`` research R1.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["ASSETS", "CONSOLE_ASSETS", "__version__"]

__version__ = "0.1.0"

#: Where the built viewer lives inside an installed copy of this package.
ASSETS = Path(__file__).parent / "assets"

#: Where the built console lives. Read by ``docdoc.api.ui`` rather than
#: recomputed there, for the reason that module's docstring already gives: two
#: copies of one fact are how the two come to disagree on the release where one
#: of them moves.
CONSOLE_ASSETS = Path(__file__).parent / "console"
