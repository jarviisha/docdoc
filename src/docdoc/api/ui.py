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

__all__ = ["absence_reason", "chosen_assets", "locate_assets"]

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


def _candidates() -> tuple[tuple[str, Path | None], ...]:
    """Where built assets may live, in the order they are preferred.

    An explicit setting wins, because a deployment that named a path meant it —
    the same precedence every other setting in this package uses.

    **A checkout's own build beats an installed distribution, and that order was
    the other way around until it cost a wrong answer.** A developer who runs
    ``npm run build`` in ``ui/`` and then starts the server from that checkout is
    asking to see what they just built. If the ``ui`` extra is also installed —
    which it is on any machine that has run ``packaging/docdoc-ui/build.sh`` or
    synced the extra — the installed copy used to win, silently, and every
    rebuild appeared to do nothing. That is not a hypothetical: it served a
    months-old bundle that predated the component library, and the stale page was
    read as evidence about current code.

    Preferring the checkout costs a real deployment nothing, because
    ``_checkout_root`` only resolves when a ``ui/dist`` directory actually sits
    four parents above this file. From a wheel in ``site-packages`` there is no
    such directory and the installed copy is reached as before; from an editable
    install there is, and it is the one the developer means.
    """
    return (
        (UI_ROOT_ENV, _configured_root()),
        ("the checkout's ui/dist", _checkout_root()),
        ("the installed docdoc-ui distribution", _installed_root()),
    )


def chosen_assets() -> tuple[str | None, Path | None]:
    """Which candidate is being served, and where it is.

    Returned as a pair so the caller can *say* which one it picked. The failure
    above was silent — three roots can hold three different builds and nothing
    named the winner — so the answer is reportable rather than merely computed.
    """
    for name, candidate in _candidates():
        found = _built(candidate)
        if found is not None:
            return name, found
    return None, None


def locate_assets() -> Path | None:
    """The directory to serve, or ``None`` if there is nothing built."""
    return chosen_assets()[1]


def absence_reason() -> str:
    """Why there is nothing to serve, and the one step that fixes it (FR-037)."""
    configured = _configured_root()
    if configured is not None:
        return (
            f"{UI_ROOT_ENV} is set to {configured} and there is no {_ENTRY_POINT} there. "
            f"Build the interface into that directory, or unset {UI_ROOT_ENV} to let "
            "docdoc find the installed assets."
        )

    # Same order as `_candidates`, so the advice names the root that would have
    # been used rather than one further down the list.
    if _checkout_root() is not None:
        return (
            "this is a source checkout and the interface has not been built. "
            "Run `npm ci && npm run build` in ui/."
        )

    if _installed_root() is not None:
        return (
            "the docdoc-ui distribution is installed but carries no built assets, which "
            "means it was built wrongly rather than that anything is missing here. "
            "Reinstall it, or set "
            f"{UI_ROOT_ENV} to a directory you built yourself."
        )

    # A guess, and last on purpose. The two checks above read real roots; this one
    # reads the working directory, which belongs to whoever started the process
    # rather than to the installation. Ordered above the installed check it
    # answered "run npm run build" to an operator whose *installed* copy was the
    # broken one, purely because they happened to be standing in a checkout.
    if (Path.cwd() / "ui" / "package.json").is_file():
        return (
            "this is a source checkout and the interface has not been built. "
            "Run `npm ci && npm run build` in ui/."
        )

    return (
        "the browser interface is not installed. Run `pip install 'docdoc[ui]'`, which "
        "brings the docdoc-ui distribution. The API is fully functional without it."
    )
