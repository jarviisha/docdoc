"""Making a directory that is actually owner-only.

One function, and it exists because ``Path.mkdir(parents=True, mode=...)`` does
not do what it reads as doing: the mode applies to the **leaf** only, and every
intermediate directory it creates gets the process default — ``0o777 & ~umask``,
which is ``0o755`` almost everywhere.

Both stores wrote ``path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)``
and got exactly that: a ``0o700`` fan-out directory holding ``0o600`` files,
inside a **world-readable** ``artifacts/`` or ``blobs/`` root. FR-044 says both
stores must be "readable only by the account that owns them" and must not write
to "a shared or world-readable location", and the roots were the one level nobody
checked — the artifact store's permissions test asserted the file and its
immediate parent, which are the two levels that were already right.

**The store root itself is deliberately left alone.** docdoc tightens the
directories it creates and not the one it was pointed at: an operator may set
``DOCDOC_STORE_ROOT`` to a directory that is shared on purpose, or to something
like ``/tmp``, and a library that chmod-ed its way up from there would break the
machine to satisfy a requirement about its own files. Everything docdoc creates
underneath is owner-only, which is what FR-044 asks of docdoc.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["DIR_MODE", "FILE_MODE", "secure_mkdir"]

#: Owner-only, on directories and files alike. FR-044: artifacts hold extracted
#: values and blobs hold whole source documents.
DIR_MODE = 0o700
FILE_MODE = 0o600


def secure_mkdir(path: Path, *, below: Path) -> None:
    """Create ``path``, and make every level docdoc owns owner-only.

    ``below`` is the store root — the directory the operator chose. Levels
    strictly under it are docdoc's to create and to tighten; ``below`` itself and
    anything above it are not touched.

    Existing directories under ``below`` are tightened too, not just newly
    created ones. A store root populated by an earlier docdoc, or a directory an
    operator made by hand, is exactly the case FR-044 is about, and skipping it
    would make the guarantee depend on who got there first. Tightening can only
    remove access, so it is the safe direction to be wrong in.

    An ``OSError`` from a chmod is swallowed. On a shared deployment the
    directory may belong to somebody else, and docdoc should not fail a run over
    a mode it cannot set — the store degrades rather than failing (FR-063), and
    the write itself reports the real problem if there is one.
    """
    path.mkdir(parents=True, exist_ok=True, mode=DIR_MODE)

    if not path.is_relative_to(below):
        # Not under the root we were given. Tighten the leaf and nothing else,
        # rather than guessing which ancestors are ours.
        _chmod(path)
        return

    level = below
    for part in path.relative_to(below).parts:
        level = level / part
        _chmod(level)


def _chmod(path: Path) -> None:
    with contextlib.suppress(OSError):
        os.chmod(path, DIR_MODE)
