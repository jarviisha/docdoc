"""The base install acquires nothing from Milestone 8 (FR-035, FR-036, SC-005).

The same argument as ``test_base_install_excludes_evaluation_data.py``, applied to
a new way of breaking it. That file was written because FR-059 "currently holds by
build configuration rather than by rule, and that is exactly the kind of guarantee
that erodes silently" — and a browser interface is a far more likely source of
silent erosion than a dataset, because the natural way to ship one is to put the
built assets in the package and be done.

**The failure this guards against is invisible in review.** A wheel that gains a
`static/` directory looks like progress; nothing errors, nothing warns, and the
only person who notices is a user wondering why a document-processing library
installed a JavaScript bundle. So the rule is a test rather than a convention,
which is what Principle XII requires of every boundary in this repository.
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tomllib

PYPROJECT = pathlib.Path("pyproject.toml")

#: Extensions that have no business in a Python distribution built from
#: `src/docdoc`. Checked by extension rather than by directory name so that
#: renaming the directory does not evade the rule.
WEB_ASSET_SUFFIXES = frozenset({".js", ".mjs", ".css", ".html", ".map", ".tsx", ".ts"})


def _config() -> dict:  # type: ignore[type-arg]
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))


def test_the_ui_source_exists_to_be_excluded() -> None:
    """The guard on the guard: excluding a directory that does not exist proves nothing."""
    assert pathlib.Path("ui/package.json").is_file()
    assert pathlib.Path("ui/src/model").is_dir()


def test_the_wheel_still_ships_only_the_python_package() -> None:
    """FR-035 — `ui/` is source for a different distribution and belongs in neither wheel."""
    packages = _config()["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"]

    assert packages == ["src/docdoc"], (
        f"the wheel ships {packages}. The browser client is built from `ui/` and "
        "distributed as `docdoc-ui`, never from here (FR-035)"
    )
    assert not any(entry.strip("/ ") == "ui" for entry in packages)


def test_the_ui_extra_exists_and_adds_only_a_dependency() -> None:
    """FR-035 — opting in delivers the files, because an extra cannot deliver files.

    The whole reason `docdoc-ui` is a separate distribution: a Python extra adds a
    dependency and nothing else, so this is the only arrangement in which the base
    install acquires nothing while `pip install docdoc[ui]` acquires an interface.
    """
    extras = _config()["project"]["optional-dependencies"]

    assert "ui" in extras, "the `ui` extra is how a deployment opts in (FR-035)"
    assert extras["ui"] == ["docdoc-ui==0.1.0"], (
        "pinned with `==` to the same version: the assets and the routes that "
        "serve them are one artifact split for packaging reasons (research R7)"
    )


def test_the_ui_extra_is_in_no_base_dependency() -> None:
    """FR-035, FR-036 — the base install stays `pydantic` and `rapidfuzz`."""
    config = _config()["project"]
    base = config["dependencies"]

    assert sorted(name.split(">=")[0] for name in base) == ["pydantic", "rapidfuzz"]
    for requirement in config["optional-dependencies"]["ui"]:
        assert requirement not in base


def test_importing_docdoc_neither_imports_nor_requires_the_asset_package() -> None:
    """FR-035 — a base install has no `docdoc_ui`, and must not care.

    Run in a subprocess so the assertion is about a fresh interpreter rather than
    about whatever this test session has already imported.
    """
    source = (
        "import sys;"
        "import docdoc;"
        "import docdoc.api.settings, docdoc.api.ui;"
        "print('docdoc_ui' in sys.modules)"
    )
    result = subprocess.run(
        [sys.executable, "-c", source], capture_output=True, text=True, check=True
    )

    assert result.stdout.strip() == "False", (
        "importing docdoc pulled in the asset package. `docdoc.api.ui` locates "
        "assets lazily and must tolerate their absence entirely (FR-037)"
    )


def test_the_package_tree_carries_no_web_asset() -> None:
    """SC-005 — zero `.js`, `.css`, or `.html` files anywhere under `src/docdoc`."""
    offenders = [
        str(path)
        for path in pathlib.Path("src/docdoc").rglob("*")
        if path.is_file() and path.suffix in WEB_ASSET_SUFFIXES
    ]

    assert offenders == [], (
        f"web assets reached the Python package: {offenders}. They belong to the "
        "`docdoc-ui` distribution built from `ui/dist` (FR-035, FR-038)"
    )


def test_no_build_output_is_committed() -> None:
    """FR-038 — `ui/dist` and the copied assets are ignored, so a checkout is clean.

    Asked of git rather than of the filesystem: the directories legitimately exist
    on a developer's machine after a build, and the requirement is that they are
    never *committed*.
    """
    tracked = subprocess.run(
        [
            "git",
            "ls-files",
            "ui/dist",
            "ui/node_modules",
            "packaging/docdoc-ui/src/docdoc_ui/assets",
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    assert tracked.stdout.strip() == "", (
        f"build output is tracked by git: {tracked.stdout.strip()}. It ships in the "
        "`docdoc-ui` wheel, built at release time from `ui/dist` (FR-038)"
    )
