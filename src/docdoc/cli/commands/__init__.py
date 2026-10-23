"""One module per command, and each one is a thin front end.

A command reads its arguments, calls a layer below, and turns what came back
into a :class:`~docdoc.cli.render.Rendering`. It computes nothing (FR-030): a
behaviour reachable only through the command line is a bug, because it is a
behaviour the HTTP interface cannot reach.

The commands operate a deployment: ``migrate``, ``worker``, ``sweep``,
``credential``, ``console-user``, ``erase`` and ``store``. Documents are
processed through the HTTP API, not here.
"""

from __future__ import annotations

__all__: list[str] = []
