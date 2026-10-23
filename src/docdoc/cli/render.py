"""Two output forms, and exactly one place that writes to standard output.

The contract in ``contracts/cli.md`` §2 is two sentences long: with ``--json``,
standard output carries exactly one JSON document and nothing else; diagnostics
go to standard error, always, in both forms (FR-027).

That is enforced structurally rather than by discipline. A command does not
print. It returns a :class:`Rendering` carrying both forms, and :func:`emit` --
the only function in the package that touches ``stdout`` -- writes one of them.
A command *cannot* interleave a banner with the JSON document, because it has
nothing to interleave it with.

**Both forms carry the same facts.** The human form is a projection of the
machine form, not a different report. Where they diverge, the machine form is
authoritative: it is the one a caller parses, and a fact that reaches only the
human form is a fact no script can act on.
"""

from __future__ import annotations

import dataclasses
import json
import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Sequence
    from typing import TextIO

__all__ = [
    "Rendering",
    "emit",
    "warn",
]


@dataclasses.dataclass(frozen=True)
class Rendering:
    """One command's output, in both forms, plus the exit code it earned.

    The exit code lives here rather than being raised or returned separately
    because it is part of what the command decided, and a caller reading the
    JSON document and a caller reading ``$?`` must never be told different
    things.
    """

    code: int
    data: dict[str, Any]
    lines: Sequence[str] = ()


def emit(rendering: Rendering, *, as_json: bool, stream: TextIO | None = None) -> int:
    """Write one form to standard output and return the exit code.

    The single write. Nothing else in :mod:`docdoc.cli` may touch ``stdout``,
    which is what makes "exactly one JSON document" true by construction rather
    than by every command remembering.
    """
    stream = sys.stdout if stream is None else stream
    if as_json:
        # `default=str` covers the enums and paths that reach here. It is a
        # deliberate blanket: a command that returns something unserialisable
        # should degrade to a readable string rather than crash after the run
        # has already been paid for.
        stream.write(json.dumps(rendering.data, indent=2, default=str, sort_keys=True))
        stream.write("\n")
    else:
        for line in rendering.lines:
            stream.write(f"{line}\n")
    stream.flush()
    return rendering.code


def warn(message: str) -> None:
    """A diagnostic, on standard error, in both output forms.

    Exists so that no command reaches for ``print`` and lands on ``stdout`` by
    reflex. The name is the whole API: if it is not the result, it is a warning,
    and warnings go to the other stream.
    """
    sys.stderr.write(f"docdoc: {message}\n")
