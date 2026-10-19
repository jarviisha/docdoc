"""Finding the operations console's built assets, or explaining their absence.

The console is optional twice over: the ``api`` extra installs the interface, and
the ``ui`` extra installs the assets it serves. Neither is in the base install and
neither ever will be — ``specs/008`` FR-035 says the base install acquires no
static asset, and a Python extra can add a dependency but cannot add files to a
wheel everyone already has. That is why the assets live in a separate
``docdoc-ui`` distribution (``specs/008`` research R7) rather than in this one.

**The absence has two shapes and they need different sentences** (FR-037). A
checkout where nobody has run the build yet is fixed by running it; an
installation missing the distribution is fixed by installing it. Telling a
developer to ``pip install`` what they have the source for, or telling an
operator to run ``npm``, is the kind of unhelpful accuracy that sends people to
read the code.

**One surface, and this module used to have two.** A ``Surface`` named tuple
parametrised every function here so the viewer and the console could share one
search over three roots. The viewer was removed (ADR-0020), and a parameter with
one argument is not flexibility — it is a reader asking "what else is there?" and
finding nothing. The three roots and their order are what mattered, and they are
unchanged.

Importable with no web framework installed, like :mod:`docdoc.api.settings` and
for the same reason: asking "where would the assets be?" should not require
FastAPI.
"""

from __future__ import annotations

import os
from pathlib import Path

from docdoc.api.settings import CONSOLE_ROOT_ENV

__all__ = [
    "absence_reason",
    "chosen_assets",
    "locate_assets",
]

#: The directory under ``ui/`` a checkout builds into.
#:
#: ``dist-console`` until ADR-0020, where it was named for the ``dist`` it sat
#: beside; with nothing beside it the qualifier described a distinction that no
#: longer exists.
_CHECKOUT_DIR = "dist"

#: The attribute on ``docdoc_ui`` holding the installed path. Still
#: ``CONSOLE_ASSETS`` over a ``console/`` directory, and deliberately **not**
#: renamed to the viewer's old ``ASSETS``/``assets``: a developer's machine can
#: still be carrying a stale ``assets/`` holding a built *viewer*, and pointing
#: the console at that name would serve it.
_INSTALLED_ATTRIBUTE = "CONSOLE_ASSETS"
_INSTALLED_DIR = "console"

#: What a real build always contains. The entry point is what tells a real build
#: from an empty directory left behind by a failed one — an empty ``dist/``
#: mounted silently is FR-037's "blank page", arrived at by a different route.
_ENTRY = "index.html"


def _configured_root() -> Path | None:
    raw = os.environ.get(CONSOLE_ROOT_ENV, "").strip()
    return Path(raw) if raw else None


def _installed_root() -> Path | None:
    """The ``docdoc-ui`` distribution, if it is installed.

    Reads that package's own constant rather than recomputing the path. This
    once rebuilt ``Path(__file__).parent / "assets"`` itself, which stated the
    same fact in two distributions — and two copies of a fact are how the two
    come to disagree, silently, on the release where one of them moves.
    """
    try:
        import docdoc_ui
    except ImportError:
        return None

    assets = getattr(docdoc_ui, _INSTALLED_ATTRIBUTE, None)
    if assets is not None:
        # The directory has to exist. Returning a path that does not is what made
        # an unbuilt console in a checkout answer "the distribution was built
        # wrongly, reinstall it" — accurate about the installed copy, and useless
        # to the developer standing in the repository. A missing directory means
        # this candidate is simply not present; a present directory with no entry
        # point is the "built wrongly" case, and `_built` still catches it.
        found = Path(assets)
        return found if found.is_dir() else None

    # An older `docdoc-ui` that predates the constant. Falling back rather than
    # failing keeps a version mismatch a missing-assets message (FR-037) instead
    # of an AttributeError from inside a request.
    location = getattr(docdoc_ui, "__file__", None)
    if location is None:
        return None
    return Path(location).parent / _INSTALLED_DIR


def _checkout_root() -> Path | None:
    """``ui/dist`` in a source checkout.

    Four parents up from this file is the repository root when running from a
    checkout, and something meaningless when running from a wheel — which is why
    the caller checks for the entry point rather than trusting the path.
    """
    candidate = Path(__file__).resolve().parents[3] / "ui" / _CHECKOUT_DIR
    return candidate if candidate.is_dir() else None


def _built(root: Path | None) -> Path | None:
    return root if root is not None and (root / _ENTRY).is_file() else None


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
        (CONSOLE_ROOT_ENV, _configured_root()),
        (f"the checkout's ui/{_CHECKOUT_DIR}", _checkout_root()),
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
            f"{CONSOLE_ROOT_ENV} is set to {configured} and there is no {_ENTRY} there. "
            f"Build the console into that directory, or unset {CONSOLE_ROOT_ENV} to let "
            "docdoc find the installed assets."
        )

    # Same order as `_candidates`, so the advice names the root that would have
    # been used rather than one further down the list.
    if _checkout_root() is not None:
        return (
            "this is a source checkout and the console has not been built. "
            "Run `npm ci && npm run build` in ui/."
        )

    if _installed_root() is not None:
        return (
            "the docdoc-ui distribution is installed but carries no build of the console, "
            "which means it was built wrongly rather than that anything is missing here. "
            "Reinstall it, or set "
            f"{CONSOLE_ROOT_ENV} to a directory you built yourself."
        )

    # A guess, and last on purpose. The two checks above read real roots; this one
    # reads the working directory, which belongs to whoever started the process
    # rather than to the installation. Ordered above the installed check it
    # answered "run npm run build" to an operator whose *installed* copy was the
    # broken one, purely because they happened to be standing in a checkout.
    if (Path.cwd() / "ui" / "package.json").is_file():
        return (
            "this is a source checkout and the console has not been built. "
            "Run `npm ci && npm run build` in ui/."
        )

    return (
        "the console is not installed. Run `pip install 'docdoc[ui]'`, which "
        "brings the docdoc-ui distribution. The API is fully functional without it."
    )
