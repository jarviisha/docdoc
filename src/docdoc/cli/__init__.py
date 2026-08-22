"""The ``docdoc`` command.

Built on :mod:`argparse`, so the base install acquires no dependency and the
command belongs to everyone who typed ``pip install docdoc``. That is the point
rather than an economy: the founding argument for having a CLI at all was that a
developer should not need to deploy five services to try docdoc, and it would be
a poor answer to make them find a second install line instead.

**What it must never contain:** extraction, grounding, or validation logic. It
parses arguments, calls :mod:`docdoc.pipeline`, and formats a result. A
behaviour reachable only through the command line is a bug.

**What it must never import:** :mod:`docdoc.api`. The two are siblings with
different audiences, held apart by an ``independence`` contract rather than by
convention -- sharing a renderer between them would be the first coupling, and
the thing they genuinely share is the result model both already import.

Two output rules make up most of the contract. With ``--json``, standard output
carries exactly one JSON document and nothing else; diagnostics go to standard
error in both forms. And the exit code distinguishes "the document is invalid"
from "docdoc could not run", because a script that confuses the two will treat a
wrong invoice as a broken tool.
"""

from __future__ import annotations

__all__: list[str] = []
