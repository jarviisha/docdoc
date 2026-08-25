"""What a fresh install actually contains.

Every other check in this repository reads the **working tree**: pytest imports
from `src/`, mypy walks `src/`, ruff walks `src/`, and import-linter resolves
against `src/`. All of them pass on a file that git has never heard of.

That is not hypothetical. `.gitignore` carried a bare ``artifacts/`` rule for a
local content-addressed store, and it also matched ``src/docdoc/artifacts/`` — so
the whole artifacts layer was untracked, ``import docdoc.pipeline`` raised
``ModuleNotFoundError`` on a fresh clone, and the commit announcing that layer
contained its four test files and none of its source. A full green gate, 94%
coverage, a clean mypy, and eight passing import contracts did not notice,
because none of them looks at what is committed.

**So these tests read git and the build, not the filesystem.** They are the only
checks here that can see the difference between "it works for me" and "it works".

The CI base-install job (`.github/workflows/ci.yml`) is the belt to this braces
and is strictly better — it installs without extras and runs the offline suite.
It is also a job that has to be *reached*: on a branch nobody has pushed, it
proves nothing. These run in the default suite, in under a second.
"""

from __future__ import annotations

import subprocess
import tomllib
import zipfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SOURCE = REPO / "src" / "docdoc"


def _packages() -> set[str]:
    """Every package under ``src/docdoc``, as a path relative to it.

    Relative paths rather than bare names, because three of these are nested —
    ``extraction/adapters``, ``ingest/parsers``, ``cli/commands`` — and a bare
    name cannot be compared against a tracked file path without matching the
    wrong directory.
    """
    return {
        # `as_posix`, not `str`. The other side of every comparison in this file
        # comes from `git ls-files` or a wheel's zip namelist, and both of those
        # speak forward slashes on every platform. `str()` of a `WindowsPath`
        # does not, so on Windows this compared `cli\commands` against
        # `cli/commands` and reported the whole tree as untracked.
        path.parent.relative_to(SOURCE).as_posix()
        for path in SOURCE.rglob("__init__.py")
        if path.parent != SOURCE and "__pycache__" not in path.parts
    }


def _tracked(*paths: str) -> set[str]:
    """What git actually has, which is the only thing an install can ship."""
    result = subprocess.run(
        ["git", "ls-files", "-z", *paths],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return {name for name in result.stdout.split("\0") if name}


def test_every_source_file_is_tracked_by_git() -> None:
    """The check that was missing, stated as plainly as it can be.

    A ``.py`` file under ``src/`` that git does not have is a file that does not
    exist for anyone else — no clone, no wheel, no CI job, no contributor.
    """
    on_disk = {
        path.relative_to(REPO).as_posix()
        for path in SOURCE.rglob("*.py")
        if "__pycache__" not in path.parts
    }
    tracked = _tracked("src/docdoc")

    untracked = sorted(on_disk - tracked)
    assert not untracked, (
        "these source files exist in the working tree and not in the repository, "
        "so a fresh clone does not have them and every other check in this suite "
        f"passes without noticing: {untracked}. Check `git check-ignore -v <path>` "
        "— a .gitignore rule written for a runtime directory can match a source "
        "one."
    )


def test_every_package_is_tracked_by_git() -> None:
    """The same check at package granularity, for a clearer failure message.

    A missing *file* is usually a forgotten `git add`. A missing *package* is
    almost always an ignore rule, and the two want different fixes.
    """
    prefix = "src/docdoc/"
    tracked_dirs = {
        Path(name).relative_to(prefix).parent.as_posix()
        for name in _tracked("src/docdoc")
        if name.startswith(prefix)
    }
    missing = sorted(_packages() - tracked_dirs)
    assert not missing, (
        f"these packages are untracked, so no install contains them: {missing}. "
        "The layer contract in pyproject.toml will still pass, because "
        "import-linter resolves against the working tree."
    )


def test_the_layer_contract_names_only_packages_that_ship() -> None:
    """Guard the other direction: a contract naming an unshipped layer is a lie.

    ``import-linter`` errors on layers that name non-existent modules, so this
    cannot currently fail — but it fails for the *right reason* if a layer is
    ever removed from the source and left in the contract, which the linter would
    report as a configuration error rather than as a packaging one.
    """
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    contracts = config["tool"]["importlinter"]["contracts"]
    layers = next(c for c in contracts if c["type"] == "layers")

    named: set[str] = set()
    for line in layers["layers"]:
        for part in line.replace(":", ",").split(","):
            cleaned = part.strip().removeprefix("docdoc.")
            if cleaned and cleaned != "docdoc":
                named.add(cleaned)

    packages = _packages()
    assert named <= packages, (
        f"the layer contract names modules that are not packages: {sorted(named - packages)}"
    )


@pytest.fixture(scope="module")
def wheel_packages(tmp_path_factory: pytest.TempPathFactory) -> set[str]:
    """Build the wheel once and report the packages inside it.

    Built with ``uv``, which this project already requires, rather than with
    ``python -m build``, which it does not — the first version of this test
    skipped on every machine that had not installed the latter, and a check that
    skips is a check that is not run.

    Module-scoped because building a wheel takes a second or two and the answer
    does not change between assertions in one session.
    """
    out = tmp_path_factory.mktemp("wheel")
    build = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(out)],
        cwd=REPO,
        capture_output=True,
        text=True,
    )
    if build.returncode != 0 and "uv" in build.stderr and "not found" in build.stderr:
        pytest.skip("uv is not on PATH")
    assert build.returncode == 0, f"building the wheel failed:\n{build.stderr[-2000:]}"

    wheels = list(out.glob("*.whl"))
    assert wheels, "the build reported success and produced no wheel"

    with zipfile.ZipFile(wheels[0]) as archive:
        return {
            Path(name).relative_to("docdoc").parent.as_posix()
            for name in archive.namelist()
            if name.startswith("docdoc/") and name.endswith(".py")
        }


def test_a_built_wheel_contains_every_package(wheel_packages: set[str]) -> None:
    """The end of the chain: what ``pip install docdoc`` actually gets.

    The one assertion in this repository that would have caught the untracked
    artifacts layer without anybody thinking to look for it.
    """
    missing = sorted(_packages() - wheel_packages)
    assert not missing, (
        f"the wheel does not contain {[f'docdoc.{m}' for m in missing]}, so "
        "`pip install docdoc` produces an installation that cannot import them. "
        f"Shipped: {sorted(wheel_packages)}"
    )


def test_the_wheel_carries_no_provider_sdk_and_no_web_framework(
    wheel_packages: set[str],
) -> None:
    """SC-013's other half, checked against the artefact rather than the tree.

    The base install must stay usable with no provider SDK at all. The adapter
    *packages* ship — they have to, since they are how a provider is reached once
    its extra is installed — and what must not ship is a hard dependency on one,
    which `pyproject.toml`'s `dependencies` list is the place to check.
    """
    config = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
    required = " ".join(config["project"]["dependencies"]).lower()

    for forbidden in ("fastapi", "uvicorn", "pymupdf", "google-genai", "azure"):
        assert forbidden not in required, (
            f"{forbidden} is a hard dependency of the base install; it belongs in "
            "an optional extra (FR-053, SC-013)"
        )

    assert "extraction/adapters" in wheel_packages
    assert "ingest/parsers" in wheel_packages
