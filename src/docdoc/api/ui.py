"""Finding the browser client's built assets, or explaining their absence.

The viewer is optional twice over: the ``api`` extra installs the interface, and
the ``ui`` extra installs the assets it serves. Neither is in the base install and
neither ever will be — FR-035 says the base install acquires no static asset, and
a Python extra can add a dependency but cannot add files to a wheel everyone
already has. That is why the assets live in a separate ``docdoc-ui``
distribution (research R7) rather than in this one.

**The absence has two shapes and they need different sentences** (FR-037). A
checkout where nobody has run the build yet is fixed by running it; an
installation missing the distribution is fixed by installing it. Telling a
developer to ``pip install`` what they have the source for, or telling an
operator to run ``npm``, is the kind of unhelpful accuracy that sends people to
read the code.

Importable with no web framework installed, like :mod:`docdoc.api.settings` and
for the same reason: asking "where would the assets be?" should not require
FastAPI.
"""

from __future__ import annotations

import os
from pathlib import Path

from docdoc.api.settings import UI_ROOT_ENV

__all__ = ["absence_reason", "locate_assets"]

#: What a built interface always contains. Used to tell a real build from an
#: empty directory left behind by a failed one — an empty `dist/` mounted
#: silently is FR-037's "blank page", arrived at by a different route.
_ENTRY_POINT = "index.html"


def _configured_root() -> Path | None:
    raw = os.environ.get(UI_ROOT_ENV, "").strip()
    return Path(raw) if raw else None


def _installed_root() -> Path | None:
    """The ``docdoc-ui`` distribution, if it is installed.

    Reads that package's own ``ASSETS`` constant rather than recomputing the
    path. Until T085 this rebuilt ``Path(__file__).parent / "assets"`` itself,
    which stated the same fact in two distributions — and two copies of a fact
    are how the two come to disagree, silently, on the release where one of them
    moves.
    """
    try:
        import docdoc_ui
    except ImportError:
        return None

    assets = getattr(docdoc_ui, "ASSETS", None)
    if assets is not None:
        return Path(assets)

    # An older `docdoc-ui` that predates the constant. Falling back rather than
    # failing keeps a version mismatch a missing-assets message (FR-037) instead
    # of an AttributeError from inside a request.
    location = getattr(docdoc_ui, "__file__", None)
    return None if location is None else Path(location).parent / "assets"


def _checkout_root() -> Path | None:
    """``ui/dist`` in a source checkout.

    Four parents up from this file is the repository root when running from a
    checkout, and something meaningless when running from a wheel — which is why
    the caller checks for the entry point rather than trusting the path.
    """
    candidate = Path(__file__).resolve().parents[3] / "ui" / "dist"
    return candidate if candidate.is_dir() else None


def _built(root: Path | None) -> Path | None:
    return root if root is not None and (root / _ENTRY_POINT).is_file() else None


def locate_assets() -> Path | None:
    """The directory to serve, or ``None`` if there is nothing built.

    Order is deliberate: an explicit setting wins, because a deployment that
    named a path meant it — the same precedence every other setting in this
    package uses.
    """
    for candidate in (_configured_root(), _installed_root(), _checkout_root()):
        found = _built(candidate)
        if found is not None:
            return found
    return None


def absence_reason() -> str:
    """Why there is nothing to serve, and the one step that fixes it (FR-037)."""
    configured = _configured_root()
    if configured is not None:
        return (
            f"{UI_ROOT_ENV} is set to {configured} and there is no {_ENTRY_POINT} there. "
            f"Build the interface into that directory, or unset {UI_ROOT_ENV} to let "
            "docdoc find the installed assets."
        )

    if _installed_root() is not None:
        return (
            "the docdoc-ui distribution is installed but carries no built assets, which "
            "means it was built wrongly rather than that anything is missing here. "
            "Reinstall it, or set "
            f"{UI_ROOT_ENV} to a directory you built yourself."
        )

    if _checkout_root() is not None or (Path.cwd() / "ui" / "package.json").is_file():
        return (
            "this is a source checkout and the interface has not been built. "
            "Run `npm ci && npm run build` in ui/."
        )

    return (
        "the browser interface is not installed. Run `pip install 'docdoc[ui]'`, which "
        "brings the docdoc-ui distribution. The API is fully functional without it."
    )
