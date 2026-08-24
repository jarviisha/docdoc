"""One module per command, and each one is a thin front end.

A command reads its arguments, calls a layer below, and turns what came back
into a :class:`~docdoc.cli.render.Rendering`. It computes nothing (FR-030): a
behaviour reachable only through the command line is a bug, because it is a
behaviour the library, the HTTP interface, and the recorder cannot reach.

The commands are the five FR-026 names plus ``store``:

===========  ==============================================================
``parse``    route, parse, and report what the parse produced
``extract``  the whole pipeline, reported as a result
``inspect``  the whole pipeline, reported as *where every value came from*
``explain``  how an artifact identity was derived, and its chain
``eval``     score a golden set
``store``    clear all of it, or one stage
===========  ==============================================================

``extract`` and ``inspect`` are the same run with two renderings, which is why
they share ``_run`` rather than each calling the pipeline their own way.
"""

from __future__ import annotations

__all__: list[str] = []
