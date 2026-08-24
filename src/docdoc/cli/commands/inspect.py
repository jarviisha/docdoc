"""``docdoc inspect FILE --schema NAME@V`` — where every value came from.

The half of the Definition of Done that answers *where did this come from*: per
field, its value, its verdict, its page, and its rectangle.

**This module is four lines of dispatch on purpose.** ``inspect`` and ``extract``
are one run with two renderings, and the run lives in
:mod:`docdoc.cli.commands.extract` beside the exit-code rule both share. Giving
``inspect`` its own call into the pipeline would have given the project two ways
to process a document, which is the shape of problem this whole milestone exists
to remove — the stage sequence is expressed in exactly one place (SC-014), and
that discipline is not suspended for a renderer.

**It does not draw.** ``inspect`` reports the page and the rectangle; rendering a
page image with boxes on it is explicitly out of scope for this feature.
"""

from __future__ import annotations

from docdoc.cli.commands.extract import inspect as run

__all__ = ["run"]
