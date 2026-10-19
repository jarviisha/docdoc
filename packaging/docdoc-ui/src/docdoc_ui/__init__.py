"""The built operations console, as an installable package.

This module holds no logic and is not imported by ``docdoc`` for its behaviour.
It exists so that ``docdoc.api.ui`` can find the assets the way Python finds
anything — by importing the package and reading its ``__file__`` — rather than by
guessing at a filesystem layout.

``console/`` is populated by ``build.sh`` from ``ui/dist`` immediately before the
wheel is built. It is absent from a source checkout on purpose: ``specs/008``
FR-038 forbids committing build output, so an installed copy of this package is
the only place the assets ever exist at rest.

**There was an ``assets/`` beside it**, holding Milestone 8's grounding viewer,
until ADR-0020 removed that surface. The console did not move into the vacated
name: the directory is gitignored, so a developer's machine can still be carrying
a stale one with a built viewer inside it, and a console that answered to
``assets`` would serve it. ``build.sh`` deletes any it finds.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["CONSOLE_ASSETS", "__version__"]

__version__ = "0.1.0"

#: Where the built console lives. Read by ``docdoc.api.ui`` rather than
#: recomputed there, for the reason that module's docstring already gives: two
#: copies of one fact are how the two come to disagree on the release where one
#: of them moves.
CONSOLE_ASSETS = Path(__file__).parent / "console"
